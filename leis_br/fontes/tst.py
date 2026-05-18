"""Scraper para Súmulas, OJs e Precedentes Normativos do TST.

Estratégia de coleta:
    API REST interna do portal de jurisprudência do TST.
    Endpoint: https://jurisprudencia-backend2.tst.jus.br/rest/pesquisa-textual/{start}/{size}
    Método POST com body ConsultaDTO filtrando por tipo (SUM, OJ, PN).

Fontes:
    API Backend: https://jurisprudencia-backend2.tst.jus.br/rest/pesquisa-textual/
    Portal SPA:  https://jurisprudencia.tst.jus.br/#/sumulas
    Config:      https://jurisprudencia.tst.jus.br/config.json
"""

import re
from collections.abc import Iterator
from datetime import datetime
from random import random

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.modelos import DocumentoColetado

_API_BACKEND = "https://jurisprudencia-backend2.tst.jus.br/rest/pesquisa-textual"
_URL_PORTAL = "https://jurisprudencia.tst.jus.br"

# Tipos de jurisprudência disponíveis na API
_TIPO_SUMULA = "SUM"
_TIPO_OJ = "OJ"
_TIPO_PN = "PN"

_RE_HTML = re.compile(r"<[^>]+>")


def _limpar_html(texto: str) -> str:
    """Remove tags HTML e normaliza espaços."""
    sem_tags = _RE_HTML.sub(" ", texto)
    return re.sub(r"\s+", " ", sem_tags).strip()


def _consulta_dto(tipo: str) -> dict:
    """Constrói o body ConsultaDTO para a API do TST.

    Args:
        tipo: Código do tipo de jurisprudência ('SUM', 'OJ', 'PN').

    Returns:
        Dicionário pronto para serializar como JSON.
    """
    return {
        "ou": None,
        "e": None,
        "termoExato": None,
        "naoContem": None,
        "ementa": None,
        "dispositivo": None,
        "numeracaoUnica": None,
        "orgaosJudicantes": [],
        "ministros": [],
        "convocados": [],
        "classesProcessuais": [],
        "indicadores": [],
        "assuntos": [],
        "tipos": [{"codigo": tipo}],
        "orgao": None,
        "publicacaoInicial": None,
        "publicacaoFinal": None,
        "julgamentoInicial": None,
        "julgamentoFinal": None,
        "ordenacao": None,
    }


class ScraperTST(ScraperBase):
    """Scraper para Súmulas, OJs e Precedentes Normativos do TST.

    Usa a API REST interna do portal jurisprudencia.tst.jus.br que aceita
    requests HTTP simples sem autenticação ou JS challenge.
    """

    def nome(self) -> str:
        return "tst"

    def _coletar_tipo(
        self, tipo: str, page_size: int = 500
    ) -> list[tuple[str, str]]:
        """Coleta itens de um tipo de jurisprudência via API REST, com paginação.

        Args:
            tipo: Código do tipo ('SUM', 'OJ', 'PN').
            page_size: Itens por página.

        Returns:
            Lista de tuplas (número, texto) ordenadas por número.
        """
        import httpx  # já é dependência do projeto

        client_headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "Content-Type": "application/json",
            "Referer": _URL_PORTAL,
            "Origin": _URL_PORTAL,
        }
        body = _consulta_dto(tipo)
        itens: list[tuple[str, str]] = []
        start = 1

        while True:
            url = f"{_API_BACKEND}/{start}/{page_size}?a={random():.6f}"
            try:
                with httpx.Client(
                    headers=client_headers,
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                ) as client:
                    resp = client.post(url, json=body)
                    resp.raise_for_status()
            except Exception as e:
                logger.warning(f"TST API POST ({tipo}) start={start}: {e}")
                break

            try:
                dados = resp.json()
            except Exception:
                logger.debug(f"TST API ({tipo}): resposta não é JSON")
                break

            if start == 1:
                total = dados.get("totalRegistros", 0)
                logger.debug(f"TST API ({tipo}): {total} registros disponíveis")

            batch = dados.get("registros", [])
            if not batch:
                break

            for reg_outer in batch:
                reg = reg_outer.get("registro", reg_outer) if isinstance(reg_outer, dict) else reg_outer
                if not isinstance(reg, dict):
                    continue
                num = str(reg.get("numero", "")).strip()
                tese = reg.get("tese", "") or ""
                titulo = reg.get("titulo", "") or ""
                tese_limpo = _limpar_html(tese)
                if not tese_limpo and titulo:
                    tese_limpo = _limpar_html(titulo)
                if num and tese_limpo and len(tese_limpo) > 10:
                    itens.append((num, tese_limpo))

            start += page_size
            total = dados.get("totalRegistros", 0)
            if start > total:
                break

        if itens:
            logger.info(f"TST API ({tipo}): {len(itens)} itens coletados")
        else:
            logger.warning(f"TST API ({tipo}): nenhum item extraído")

        return sorted(itens, key=lambda x: int(x[0]) if x[0].isdigit() else 0)

    def _fazer_documento(
        self,
        itens: list[tuple[str, str]],
        titulo: str,
        url: str,
        area: str | None = "trabalhista",
        prefixo: str = "Súmula",
    ) -> DocumentoColetado:
        """Cria DocumentoColetado a partir de lista de itens.

        Args:
            itens: Lista de tuplas (número, texto).
            titulo: Título do documento.
            url: URL de origem.
            area: Área jurídica.
            prefixo: Prefixo para cada item.

        Returns:
            DocumentoColetado preenchido.
        """
        linhas = [f"Art. {num}º {prefixo} {num}: {txt}" for num, txt in itens]
        texto = "\n".join(linhas)
        return DocumentoColetado(
            url_origem=url,
            fonte=titulo,
            tipo="sumula",
            area=area,
            titulo=titulo,
            texto=texto,
            data_publicacao=None,
            data_coleta=datetime.now(),
            hash_conteudo=self._hash(texto),
            orgao="TST",
        )

    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta Súmulas, OJs e Precedentes Normativos do TST."""
        logger.info("TST: iniciando coleta via API jurisprudencia-backend2.tst.jus.br")
        url_ref = f"{_URL_PORTAL}/#/sumulas"

        # ── Súmulas ───────────────────────────────────────────────────
        sumulas = self._coletar_tipo(_TIPO_SUMULA)
        if sumulas:
            yield self._fazer_documento(
                sumulas,
                titulo="Súmulas TST",
                url=url_ref,
                prefixo="Súmula TST",
            )
        else:
            logger.warning("TST: súmulas indisponíveis via API backend.")

        # ── Orientações Jurisprudenciais ───────────────────────────────
        ojs = self._coletar_tipo(_TIPO_OJ)
        if ojs:
            yield self._fazer_documento(
                ojs,
                titulo="Orientações Jurisprudenciais TST",
                url=f"{_URL_PORTAL}/#/orientacoes-jurisprudenciais",
                prefixo="OJ TST",
            )

        # ── Precedentes Normativos ─────────────────────────────────────
        pns = self._coletar_tipo(_TIPO_PN)
        if pns:
            yield self._fazer_documento(
                pns,
                titulo="Precedentes Normativos TST",
                url=f"{_URL_PORTAL}/#/precedentes-normativos",
                prefixo="PN TST",
            )

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta tudo (TST não oferece feed incremental)."""
        yield from self.coletar()
