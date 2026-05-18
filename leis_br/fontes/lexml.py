"""Scraper para legislação federal adicional via Planalto.gov.br.

Complementa o ScraperPlanalto com leis relevantes fora do conjunto
principal de 19 leis prioritárias, aumentando a cobertura do RAG
jurídico para temas trabalhistas, previdenciários, administrativos e penais.

A API SRU do LexML (lexml.gov.br/busca/SRU) foi descontinuada em 2023 e
retorna 404. A API do Senado Federal (legis.senado.leg.br/dadosabertos)
fornece apenas metadados, sem texto completo. A alternativa mais confiável
é buscar diretamente do Planalto, que já demonstrou funcionar no scraper
principal.

O nome do scraper ('lexml') é mantido por compatibilidade com o pipeline.

Fontes:
    Todas as URLs de legislação compilada: https://www.planalto.gov.br/ccivil_03/
"""

from collections.abc import Iterator
from datetime import datetime

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.fontes.planalto import extrair_texto_planalto
from leis_br.modelos import DocumentoColetado

# Legislação adicional relevante não coberta pelo ScraperPlanalto.
# Formato: título → (URL Planalto, área jurídica, data publicação ISO)
LEIS_COMPLEMENTARES: dict[str, tuple[str, str | None, str | None]] = {
    "Estatuto do Idoso (Lei 10.741/2003)": (
        "https://www.planalto.gov.br/ccivil_03/leis/2003/l10.741.htm",
        "civil",
        "2003-10-01",
    ),
    "Lei Brasileira de Inclusão (Lei 13.146/2015)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13146.htm",
        "civil",
        "2015-07-06",
    ),
    "Lei de Crimes Hediondos (Lei 8.072/1990)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8072.htm",
        "penal",
        "1990-07-25",
    ),
    "Lei de Drogas (Lei 11.343/2006)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11343.htm",
        "penal",
        "2006-08-23",
    ),
    "Lei de Abuso de Autoridade (Lei 13.869/2019)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2019/lei/l13869.htm",
        "penal",
        "2019-09-05",
    ),
    "Lei de Execução Fiscal (Lei 6.830/1980)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l6830.htm",
        "tributario",
        "1980-09-22",
    ),
    "Lei de Arbitragem (Lei 9.307/1996)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l9307.htm",
        "civil",
        "1996-09-23",
    ),
    "Lei de Mediação (Lei 13.140/2015)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13140.htm",
        "civil",
        "2015-06-26",
    ),
    "Estatuto do Desarmamento (Lei 10.826/2003)": (
        "https://www.planalto.gov.br/ccivil_03/leis/2003/l10.826.htm",
        "penal",
        "2003-12-22",
    ),
    "Lei de Falências (Lei 11.101/2005)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2005/lei/l11101.htm",
        "civil",
        "2005-02-09",
    ),
    "Código Florestal (Lei 12.651/2012)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2012/lei/l12651.htm",
        "ambiental",
        "2012-05-25",
    ),
    "Lei do FGTS (Lei 8.036/1990)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8036consol.htm",
        "trabalhista",
        "1990-05-11",
    ),
    "Lei da Previdência Social (Lei 8.213/1991)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8213cons.htm",
        "previdenciario",
        "1991-07-24",
    ),
    "Lei dos Servidores Públicos (Lei 8.112/1990)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8112cons.htm",
        "administrativo",
        "1990-12-11",
    ),
    "Lei de Responsabilidade Fiscal (LC 101/2000)": (
        "https://www.planalto.gov.br/ccivil_03/leis/lcp/lcp101.htm",
        "administrativo",
        "2000-05-04",
    ),
    "Código de Trânsito Brasileiro (Lei 9.503/1997)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm",
        "civil",
        "1997-09-23",
    ),
    "Estatuto das Cidades (Lei 10.257/2001)": (
        "https://www.planalto.gov.br/ccivil_03/leis/leis_2001/l10257.htm",
        "administrativo",
        "2001-07-10",
    ),
    "Lei das Organizações Criminosas (Lei 12.850/2013)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12850.htm",
        "penal",
        "2013-08-02",
    ),
    "Lei dos Planos de Saúde (Lei 9.656/1998)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l9656.htm",
        "civil",
        "1998-06-03",
    ),
    "Lei de Acesso à Informação (Lei 12.527/2011)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2011/lei/l12527.htm",
        "administrativo",
        "2011-11-18",
    ),
    "Lei Anticorrupção (Lei 12.846/2013)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2013/lei/l12846.htm",
        "administrativo",
        "2013-08-01",
    ),
    "Lei de Alienação Fiduciária (Lei 9.514/1997)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l9514.htm",
        "civil",
        "1997-11-20",
    ),
    "Lei do Inquilinato (Lei 8.245/1991)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8245.htm",
        "civil",
        "1991-10-18",
    ),
}


def _verificar_bs4() -> None:
    try:
        import bs4  # noqa: F401
        import lxml  # noqa: F401
    except ImportError:
        raise ImportError(
            "Scrapers requerem dependências extras.\n"
            "Execute: pip install leis-br[scrapers]"
        )


class ScraperLexML(ScraperBase):
    """Coleta legislação complementar via Planalto.gov.br.

    O nome 'lexml' é mantido para compatibilidade com o pipeline.
    Busca 23 leis relevantes não cobertas pelo ScraperPlanalto principal,
    ampliando o corpus para áreas trabalhista, previdenciária, penal e civil.

    Note:
        A API SRU do LexML foi descontinuada em 2023. A API do Senado
        Federal fornece apenas metadados sem texto completo. A fonte
        Planalto.gov.br é a alternativa mais confiável para texto integral.
    """

    def nome(self) -> str:
        return "lexml"

    def _coletar_lei(
        self,
        titulo: str,
        url: str,
        area: str | None,
        data_pub_iso: str | None,
    ) -> DocumentoColetado | None:
        """Coleta e extrai texto de uma lei do Planalto.

        Args:
            titulo: Título da lei para identificação.
            url: URL da lei compilada no Planalto.
            area: Área jurídica (ex: 'penal', 'civil').
            data_pub_iso: Data de publicação em formato ISO AAAA-MM-DD.

        Returns:
            DocumentoColetado ou None se falhar ou texto for insuficiente.
        """
        _verificar_bs4()

        resp = self._http_get(url)
        if resp is None:
            return None

        content = resp.content
        if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
            html = content.decode("utf-16", errors="replace")
        else:
            try:
                html = resp.text
            except Exception:
                html = content.decode("latin-1", errors="replace")

        try:
            texto = extrair_texto_planalto(html)
        except ImportError:
            raise
        except Exception as e:
            logger.error(f"LexML: erro ao extrair texto de '{titulo}': {e}")
            return None

        if len(texto) < 200:
            logger.warning(f"LexML: texto muito curto para '{titulo}' — ignorando")
            return None

        data_publicacao: datetime | None = None
        if data_pub_iso:
            try:
                data_publicacao = datetime.fromisoformat(data_pub_iso)
            except ValueError:
                pass

        logger.info(f"LexML: coletado '{titulo}' ({len(texto)} chars)")
        return DocumentoColetado(
            url_origem=url,
            fonte=titulo,
            tipo="lei_federal",
            area=area,
            titulo=titulo,
            texto=texto,
            data_publicacao=data_publicacao,
            data_coleta=datetime.now(),
            hash_conteudo=self._hash(texto),
            orgao="Congresso Nacional",
        )

    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta as 23 leis complementares do Planalto.gov.br."""
        logger.info(f"LexML: iniciando coleta de {len(LEIS_COMPLEMENTARES)} leis complementares")
        for titulo, (url, area, data_pub) in LEIS_COMPLEMENTARES.items():
            doc = self._coletar_lei(titulo, url, area, data_pub)
            if doc:
                yield doc

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta todas as leis (Planalto não oferece feed de alterações)."""
        yield from self.coletar()
