"""Scraper para Súmulas do STF (Supremo Tribunal Federal).

Estratégia de coleta:
    1. API REST interna do portal jurisprudencia.stf.jus.br (via Playwright)
       — portal usa AWS WAF JS Challenge; Playwright headless bypassa o challenge.
       Endpoint: POST /api/search/search com query ES { "query": {"term": {"base": "sumulas"}} }.
    2. Portal legado www.stf.jus.br (HTML estático) — fallback.

Fontes:
    Portal SPA:   https://jurisprudencia.stf.jus.br/
    API Search:   https://jurisprudencia.stf.jus.br/api/search/search
    Portal legado: https://www.stf.jus.br/portal/jurisprudencia/menuSumario.asp?tipo=1
"""

import re
from collections.abc import Iterator
from datetime import datetime

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.modelos import DocumentoColetado

_URL_PORTAL = "https://jurisprudencia.stf.jus.br"
_API_SEARCH = "/api/search/search"
_PAGE_SIZE = 250  # limite máximo da API (403 se > 250)

_URL_LEGADO_ORDINARIAS = (
    "https://www.stf.jus.br/portal/jurisprudencia/menuSumario.asp?tipo=1"
)
_URL_LEGADO_VINCULANTES = (
    "https://www.stf.jus.br/portal/jurisprudencia/menuSumario.asp?tipo=3"
)

_RE_SUMULA = re.compile(
    r"S[úu]mula\s+(?:Vinculante\s+)?(?:n[ºo°]?\s*)?(\d+)[°º]?\s*[-–.:]\s*([^\n<]{15,800})",
    re.IGNORECASE,
)
_RE_ITEM_NUMERADO = re.compile(
    r"^\s*(\d{1,4})\s*[-–.:]\s*(.{20,600})\s*$",
    re.MULTILINE,
)


def _verificar_bs4() -> None:
    try:
        import bs4  # noqa: F401
    except ImportError:
        raise ImportError(
            "Scrapers requerem dependências extras.\n"
            "Execute: pip install leis-br[scrapers]"
        )


def _extrair_sumulas_html(html: str, vinculante: bool = False) -> list[tuple[str, str]]:
    """Extrai tuplas (número, texto) de uma página HTML de súmulas do STF."""
    _verificar_bs4()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    texto = soup.get_text(separator="\n", strip=True)

    sumulas = [
        (m.group(1), m.group(2).strip())
        for m in _RE_SUMULA.finditer(texto)
        if len(m.group(2).strip()) > 15
    ]

    if not sumulas:
        sumulas = [
            (m.group(1), m.group(2).strip())
            for m in _RE_ITEM_NUMERADO.finditer(texto)
            if len(m.group(2).strip()) > 20
        ]

    return sumulas


def _sumulas_para_texto(sumulas: list[tuple[str, str]], vinculante: bool) -> str:
    prefixo = "Súmula Vinculante" if vinculante else "Súmula"
    return "\n".join(f"Art. {num}º {prefixo} {num}: {txt}" for num, txt in sumulas)


class ScraperSTF(ScraperBase):
    """Scraper para Súmulas ordinárias e vinculantes do STF.

    Usa a API REST interna do portal jurisprudencia.stf.jus.br via Playwright
    headless (necessário para passar o AWS WAF JS Challenge).

    Note:
        VERIFY_SSL=False necessário: portal STF usa certificados
        ICP-Brasil não incluídos no bundle padrão do Python/httpx.
    """

    VERIFY_SSL = False

    def nome(self) -> str:
        return "stf"

    def _coletar_via_api_playwright(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Coleta todas as súmulas (ordinárias e vinculantes) via API ES.

        Usa Playwright para bypassar o AWS WAF challenge e faz chamadas
        fetch() dentro do contexto do browser.

        Returns:
            Tupla (sumulas_ordinarias, sumulas_vinculantes).
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.debug("Playwright não instalado.")
            return [], []

        import json as _json

        ordinarias: list[tuple[str, str]] = []
        vinculantes: list[tuple[str, str]] = []
        total_fetched = 0

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
                    locale="pt-BR",
                )
                page = ctx.new_page()
                logger.info("STF: carregando portal para bypassar AWS WAF...")
                page.goto(
                    _URL_PORTAL + "/",
                    wait_until="networkidle",
                    timeout=60_000,
                )
                page.wait_for_timeout(2000)  # aguarda Angular completar init

                # Paginate through all sumulas
                offset = 0
                while True:
                    request_body = {
                        "query": {"term": {"base": "sumulas"}},
                        "size": _PAGE_SIZE,
                        "from": offset,
                    }

                    result = page.evaluate(
                        """
                        async (req) => {
                            try {
                                const resp = await fetch(req.path, {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                                    body: JSON.stringify(req.body)
                                });
                                if (!resp.ok) { const t = await resp.text(); return {ok: false, status: resp.status, text: t.substring(0, 200)}; }
                                return {ok: true, status: resp.status, text: await resp.text()};
                            } catch(e) {
                                return {ok: false, status: 0, text: 'ERR: ' + e.message};
                            }
                        }
                        """,
                        {"path": _API_SEARCH, "body": request_body},
                    )

                    if not result.get("ok") or not result.get("text"):
                        logger.warning(
                            f"STF API: status={result.get('status')}, "
                            f"text={result.get('text', '')[:200]}"
                        )
                        break

                    try:
                        data = _json.loads(result["text"])
                    except Exception:
                        logger.debug("STF API: resposta não é JSON")
                        break

                    hits_data = data.get("result", {}).get("hits", {})
                    total = hits_data.get("total", {}).get("value", 0)
                    hits = hits_data.get("hits", [])

                    if offset == 0:
                        logger.debug(f"STF API: {total} súmulas disponíveis")

                    if not hits:
                        break

                    for hit in hits:
                        src = hit.get("_source", hit)
                        num = str(src.get("sumula_numero", "")).strip()
                        texto = str(src.get("sumula_texto", "")).strip()
                        is_vinc = src.get("is_vinculante", False)

                        if num and texto and len(texto) > 10:
                            total_fetched += 1
                            if is_vinc:
                                vinculantes.append((num, texto))
                            else:
                                ordinarias.append((num, texto))

                    offset += _PAGE_SIZE
                    if offset >= total:
                        break

                browser.close()
        except Exception as e:
            logger.warning(f"STF Playwright: {e}")
            return [], []

        logger.info(
            f"STF API: {len(ordinarias)} ordinárias, {len(vinculantes)} vinculantes coletadas"
        )
        return ordinarias, vinculantes

    def _coletar_via_portal_legado(
        self, url: str, titulo: str, vinculante: bool
    ) -> DocumentoColetado | None:
        """Coleta súmulas do portal legado estático www.stf.jus.br."""
        resp = self._http_get(url)
        if resp is None:
            return None

        if (
            "portal.stf.jus.br" in resp.text
            or "You need to enable JavaScript" in resp.text
            or len(resp.text) < 500
        ):
            logger.warning(
                f"STF portal legado: {url} redirecionou para SPA ou retornou página vazia."
            )
            return None

        try:
            sumulas = _extrair_sumulas_html(resp.text, vinculante=vinculante)
        except ImportError:
            raise
        except Exception as e:
            logger.error(f"STF portal legado: erro ao extrair súmulas de {url}: {e}")
            return None

        if not sumulas:
            logger.warning(f"STF portal legado: nenhuma súmula extraída de {url}")
            return None

        texto = _sumulas_para_texto(sumulas, vinculante)
        tipo_str = "Vinculantes" if vinculante else "Ordinárias"
        titulo_doc = f"Súmulas Vinculantes STF" if vinculante else "Súmulas STF"
        logger.info(f"STF portal legado: {len(sumulas)} súmulas {tipo_str} coletadas")

        return DocumentoColetado(
            url_origem=url,
            fonte=titulo_doc,
            tipo="sumula",
            area=None,
            titulo=titulo_doc,
            texto=texto,
            data_publicacao=None,
            data_coleta=datetime.now(),
            hash_conteudo=self._hash(texto),
            orgao="STF",
        )

    def _fazer_documento(
        self,
        sumulas: list[tuple[str, str]],
        titulo: str,
        url: str,
        vinculante: bool,
    ) -> DocumentoColetado:
        texto = _sumulas_para_texto(sumulas, vinculante)
        return DocumentoColetado(
            url_origem=url,
            fonte=titulo,
            tipo="sumula",
            area=None,
            titulo=titulo,
            texto=texto,
            data_publicacao=None,
            data_coleta=datetime.now(),
            hash_conteudo=self._hash(texto),
            orgao="STF",
        )

    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta súmulas ordinárias e vinculantes do STF."""
        logger.info("STF: iniciando coleta de súmulas")

        ordinarias, vinculantes = self._coletar_via_api_playwright()

        if ordinarias:
            yield self._fazer_documento(
                ordinarias,
                titulo="Súmulas STF",
                url=_URL_LEGADO_ORDINARIAS,
                vinculante=False,
            )
        else:
            # Fallback: portal legado
            doc = self._coletar_via_portal_legado(
                _URL_LEGADO_ORDINARIAS, "Súmulas STF", False
            )
            if doc:
                yield doc
            else:
                logger.warning(
                    "STF: súmulas ordinárias indisponíveis. "
                    "Instale Playwright: pip install leis-br[playwright] && playwright install chromium"
                )

        if vinculantes:
            yield self._fazer_documento(
                vinculantes,
                titulo="Súmulas Vinculantes STF",
                url=_URL_LEGADO_VINCULANTES,
                vinculante=True,
            )
        else:
            doc = self._coletar_via_portal_legado(
                _URL_LEGADO_VINCULANTES, "Súmulas Vinculantes STF", True
            )
            if doc:
                yield doc

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta todas as súmulas (STF não oferece feed incremental)."""
        yield from self.coletar()
