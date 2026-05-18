"""Scraper para Leis do Estado do Rio de Janeiro (ALERJ).

Estratégia de coleta:
    1. Portal www3.alerj.rj.gov.br age como proxy para o Lotus Notes/Domino
       em alerjln1.alerj.rj.gov.br (CONTLEI.NSF).
    2. A URL base é: http://www3.alerj.rj.gov.br/lotus_notes/default.asp?id={ID}&url={BASE64}
       onde BASE64 é a URL do caminho Domino codificada em base64.
    3. As views Domino listam leis em páginas de 100, com parâmetros Start= e Count=.
    4. Cada lei tem um link ?OpenDocument que retorna o texto completo.

Fontes coletadas:
    - Leis Ordinárias  (CONTLEI.NSF / LeiOrdInt,  page_id=53): ~11.000 leis (1968-atual)
    - Leis Complementares (CONTLEI.NSF / LeiCompInt, page_id=52): ~230 leis (1975-atual)

Nota: alerjln1.alerj.rj.gov.br não responde a requisições HTTP externas diretas.
O proxy www3 (Microsoft IIS) faz as chamadas internas ao Domino.
"""

import base64
import re
from collections.abc import Iterator
from datetime import datetime

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.modelos import DocumentoColetado

_WWW3_BASE = "http://www3.alerj.rj.gov.br/lotus_notes/default.asp"

# Page IDs no portal www3 → view Domino
_FONTES = [
    {"page_id": 53, "view": "LeiOrdInt", "tipo": "lei_ordinaria", "label": "Lei Ordinária"},
    {"page_id": 52, "view": "LeiCompInt", "tipo": "lei_complementar", "label": "Lei Complementar"},
]

# Status de leis inválidas (não indexar)
_STATUS_INVALIDOS = {"revogado", "revogada", "cancelado", "cancelada"}


def _b64(path: str) -> str:
    return base64.b64encode(path.encode()).decode()


def _proxy_url(page_id: int, path: str) -> str:
    return f"{_WWW3_BASE}?id={page_id}&url={_b64(path)}"


def _parse_listing(html: str) -> list[dict]:
    """Extrai linhas da tabela de listagem de leis do HTML do proxy www3.

    Returns:
        Lista de dicts com keys: numero, ano, status, ementa, doc_path
    """
    rows = re.findall(r"<tr>(.*?)</tr>", html, re.S | re.I)
    result = []
    for row in rows:
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)
        texts = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
        texts = [re.sub(r"\s+", " ", t).strip() for t in texts]
        texts = [t for t in texts if t]

        # Linha de dados: primeira célula é número (dígitos), pelo menos 3 colunas
        if len(texts) < 3 or not texts[0].isdigit():
            continue

        # Extrair link do documento (data-role="/contlei.nsf/...?OpenDocument")
        doc_links = re.findall(r'data-role=["\']([^"\']*\?OpenDocument)["\']', row, re.I)
        doc_path = doc_links[0] if doc_links else None

        result.append(
            {
                "numero": texts[0],
                "ano": texts[1] if len(texts) > 1 else "",
                "status": texts[2] if len(texts) > 2 else "",
                "ementa": texts[3] if len(texts) > 3 else "",
                "autoria": texts[4] if len(texts) > 4 else "",
                "doc_path": doc_path,
            }
        )
    return result


def _parse_doc_text(html: str) -> str:
    """Extrai texto limpo de um documento de lei do HTML do proxy www3."""
    # Remove scripts, styles, navigation (header/footer)
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.S | re.I)
    # Remove nav/menu areas (class="navbar", id="menu", etc.)
    html = re.sub(r'<nav[^>]*>.*?</nav>', "", html, flags=re.S | re.I)

    # Strip tags
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&[a-zA-Z#0-9]+;", _decode_html_entity, text)
    text = re.sub(r"\s+", " ", text).strip()

    # Find law body: starts at "Faço saber" or "Art. 1" or governor preamble
    for marker in ["Faço saber", "Art. 1", "ARTIGO 1"]:
        idx = text.find(marker)
        if idx > 0:
            # Keep some context before marker (up to 200 chars for title/number)
            start = max(0, idx - 200)
            text = text[start:]
            break

    # Truncate after "Ficha Técnica" footer section
    for footer in ["Ficha Técnica", "FICHA TÉCNICA"]:
        idx = text.find(footer)
        if idx > 0:
            text = text[:idx].strip()
            break

    return text.strip()


_HTML_ENTITIES = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&apos;": "'",
    "&nbsp;": " ",
    "&#8220;": "\u201c",
    "&#8221;": "\u201d",
    "&#8216;": "\u2018",
    "&#8217;": "\u2019",
    "&#8212;": "\u2014",
    "&#8211;": "\u2013",
}


def _decode_html_entity(match: re.Match) -> str:  # type: ignore[type-arg]
    entity = match.group(0)
    if entity in _HTML_ENTITIES:
        return _HTML_ENTITIES[entity]
    if entity.startswith("&#") and entity.endswith(";"):
        try:
            code = int(entity[2:-1])
            return chr(code)
        except ValueError:
            pass
    return entity


class ScraperALERJ(ScraperBase):
    """Scraper para Leis Ordinárias e Complementares da ALERJ.

    Coleta via portal www3.alerj.rj.gov.br que serve como proxy reverso
    para o banco Lotus Notes/Domino (CONTLEI.NSF) em alerjln1.alerj.rj.gov.br.

    Cobre leis promulgadas desde 1968 (Leis Ordinárias) e 1975 (Leis Complementares).
    """

    # www3 é um servidor lento — timeout maior
    TIMEOUT = 45.0
    DELAY_ENTRE_REQUISICOES = 1.0

    def nome(self) -> str:
        return "alerj"

    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta todas as leis ativas da ALERJ."""
        for fonte in _FONTES:
            yield from self._coletar_fonte(
                page_id=fonte["page_id"],
                view=fonte["view"],
                tipo=fonte["tipo"],
                label=fonte["label"],
                apenas_desde=None,
            )

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta leis publicadas após a data informada.

        Como o Domino não oferece feed incremental por data de publicação,
        fazemos varredura da primeira página (leis mais recentes) até encontrar
        uma lei publicada antes de 'desde'.
        """
        for fonte in _FONTES:
            yield from self._coletar_fonte(
                page_id=fonte["page_id"],
                view=fonte["view"],
                tipo=fonte["tipo"],
                label=fonte["label"],
                apenas_desde=desde,
            )

    def _coletar_fonte(
        self,
        page_id: int,
        view: str,
        tipo: str,
        label: str,
        apenas_desde: datetime | None,
    ) -> Iterator[DocumentoColetado]:
        """Pagina pela view Domino e yields um DocumentoColetado por lei."""
        logger.info(f"ALERJ: iniciando coleta de {label} (view={view})")
        total = 0
        ignorados = 0
        start = 1

        while True:
            path = f"/contlei.nsf/{view}?OpenForm&Count=100&Start={start}"
            url = _proxy_url(page_id, path)
            resp = self._http_get(url)
            if resp is None:
                logger.error(f"ALERJ: falha ao buscar listagem {view} Start={start}")
                break

            leis = _parse_listing(resp.text)
            if not leis:
                logger.debug(f"ALERJ: {view} Start={start} — sem mais leis, encerrando")
                break

            for lei in leis:
                status_lower = lei["status"].lower()

                # Pular leis revogadas/canceladas
                if status_lower in _STATUS_INVALIDOS:
                    ignorados += 1
                    continue

                doc = self._coletar_documento(lei, page_id, tipo, label, apenas_desde)
                if doc is None:
                    continue
                if doc is False:  # type: ignore[comparison-overlap]
                    # Sinal de parada incremental
                    logger.info(
                        f"ALERJ: {label} — encontradas leis anteriores a {apenas_desde}, parando"
                    )
                    return
                total += 1
                yield doc  # type: ignore[misc]

            start += 100

        logger.info(
            f"ALERJ: {label} — {total} documentos coletados, {ignorados} ignorados (revogados)"
        )

    def _coletar_documento(
        self,
        lei: dict,
        page_id: int,
        tipo: str,
        label: str,
        apenas_desde: datetime | None,
    ) -> "DocumentoColetado | bool | None":
        """Busca o texto completo de uma lei e retorna DocumentoColetado.

        Returns:
            DocumentoColetado em sucesso,
            None para pular sem parar,
            False para parar varredura incremental.
        """
        if not lei.get("doc_path"):
            logger.debug(f"ALERJ: lei {lei['numero']}/{lei['ano']} sem link de documento")
            return None

        url = _proxy_url(page_id, lei["doc_path"])
        resp = self._http_get(url)
        if resp is None:
            logger.warning(f"ALERJ: falha ao buscar lei {lei['numero']}/{lei['ano']}")
            return None

        texto = _parse_doc_text(resp.text)
        if len(texto) < 50:
            logger.warning(
                f"ALERJ: lei {lei['numero']}/{lei['ano']} retornou texto vazio ou muito curto"
            )
            return None

        # Tentar extrair data de publicação do texto
        data_pub = _extrair_data_publicacao(resp.text, lei["ano"])

        # Parada incremental: se a lei é mais antiga que 'desde', sinaliza parar
        if apenas_desde and data_pub and data_pub < apenas_desde:
            return False  # type: ignore[return-value]

        numero_fmt = f"{label} nº {lei['numero']}/{lei['ano']}"
        titulo = lei["ementa"][:120] if lei["ementa"] else numero_fmt

        return DocumentoColetado(
            url_origem=f"http://alerjln1.alerj.rj.gov.br{lei['doc_path']}",
            fonte=f"ALERJ — {label}",
            tipo=tipo,
            area="estadual_rj",
            titulo=titulo,
            texto=texto,
            data_publicacao=data_pub,
            data_coleta=datetime.now(),
            vigencia="ativa" if "vigor" in lei["status"].lower() else "parcialmente_revogada",
            numero_lei=numero_fmt,
            hash_conteudo=self._hash(texto),
            orgao="ALERJ",
        )


def _extrair_data_publicacao(html: str, ano_fallback: str) -> datetime | None:
    """Extrai a data de publicação do HTML da lei (campo 'Data de publicação').

    No Domino o valor fica em uma <td> separada do label, como:
        Data de publicação</...></td><td ...>12/03/2026</td>
    """
    match = re.search(
        r"Data de publica[çc][ãa]o.*?(\d{2}/\d{2}/\d{4})",
        html,
        re.I | re.S,
    )
    if match:
        try:
            return datetime.strptime(match.group(1), "%d/%m/%Y")
        except ValueError:
            pass

    # Fallback: primeiro dia do ano
    if ano_fallback.isdigit():
        try:
            return datetime(int(ano_fallback), 1, 1)
        except ValueError:
            pass
    return None
