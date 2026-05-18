"""Scraper para legislação federal do portal Planalto.

Coleta as principais leis federais compiladas (versão atualizada com
todas as alterações) diretamente do portal planalto.gov.br.

Fontes:
    Legislação compilada: https://www.planalto.gov.br/ccivil_03/
"""

import re
from collections.abc import Iterator
from datetime import date, datetime

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.modelos import DocumentoColetado


# Mapeamento: título → (URL, área_jurídica, data_publicação_ISO)
LEIS_PRIORITARIAS: dict[str, tuple[str, str | None, str | None]] = {
    "Constituição Federal/1988": (
        "https://www.planalto.gov.br/ccivil_03/constituicao/constituicao.htm",
        "constitucional",
        "1988-10-05",
    ),
    "Código Civil (Lei 10.406/2002)": (
        "https://www.planalto.gov.br/ccivil_03/leis/2002/l10406compilada.htm",
        "civil",
        "2002-01-10",
    ),
    "Código Penal (Decreto-Lei 2.848/1940)": (
        "https://www.planalto.gov.br/ccivil_03/decreto-lei/del2848compilado.htm",
        "penal",
        "1940-12-07",
    ),
    "CLT (Decreto-Lei 5.452/1943)": (
        "https://www.planalto.gov.br/ccivil_03/decreto-lei/del5452.htm",
        "trabalhista",
        "1943-05-01",
    ),
    "CDC (Lei 8.078/1990)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8078compilado.htm",
        "consumidor",
        "1990-09-11",
    ),
    "LGPD (Lei 13.709/2018)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/l13709.htm",
        "dados",
        "2018-08-14",
    ),
    "CPC (Lei 13.105/2015)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2015/lei/l13105.htm",
        "processual_civil",
        "2015-03-16",
    ),
    "CPP (Decreto-Lei 3.689/1941)": (
        "https://www.planalto.gov.br/ccivil_03/decreto-lei/del3689compilado.htm",
        "processual_penal",
        "1941-10-03",
    ),
    "ECA (Lei 8.069/1990)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8069.htm",
        "civil",
        "1990-07-13",
    ),
    "CTN (Lei 5.172/1966)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l5172compilado.htm",
        "tributario",
        "1966-10-25",
    ),
    "Lei de Licitações (Lei 14.133/2021)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2019-2022/2021/lei/l14133.htm",
        "administrativo",
        "2021-04-01",
    ),
    "Marco Civil da Internet (Lei 12.965/2014)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2011-2014/2014/lei/l12965.htm",
        "digital",
        "2014-04-23",
    ),
    "Lei Maria da Penha (Lei 11.340/2006)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2004-2006/2006/lei/l11340.htm",
        "penal",
        "2006-08-07",
    ),
    "Lei de Crimes Ambientais (Lei 9.605/1998)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l9605.htm",
        "ambiental",
        "1998-02-12",
    ),
    "Estatuto da OAB (Lei 8.906/1994)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8906.htm",
        "administrativo",
        "1994-07-04",
    ),
    "Lei de Execução Penal (Lei 7.210/1984)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l7210compilado.htm",
        "penal",
        "1984-07-11",
    ),
    "Lei de Improbidade Administrativa (Lei 8.429/1992)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l8429.htm",
        "administrativo",
        "1992-06-02",
    ),
    "Lei do Mandado de Segurança (Lei 12.016/2009)": (
        "https://www.planalto.gov.br/ccivil_03/_ato2007-2010/2009/lei/l12016.htm",
        "constitucional",
        "2009-08-07",
    ),
    "Código Eleitoral (Lei 4.737/1965)": (
        "https://www.planalto.gov.br/ccivil_03/leis/l4737.htm",
        "eleitoral",
        "1965-07-15",
    ),
}

# Padrão de artigo no início de linha
_RE_ARTIGO_INICIO = re.compile(r"^Art\.\s*\d+", re.MULTILINE)


def _verificar_bs4() -> None:
    try:
        import bs4  # noqa: F401
        import lxml  # noqa: F401
    except ImportError:
        raise ImportError(
            "Scrapers requerem dependências extras.\n"
            "Execute: pip install leis-br[scrapers]"
        )


_MARCADOR_REVOGADO = "(REVOGADO)"


def _marcar_strike(soup) -> None:
    """Substitui conteúdo em <strike>/<s> por marcador de revogação.

    O portal do Planalto/Senado usa <strike> ou <s> para indicar dispositivos
    revogados dentro de leis que continuam vigentes. Exemplo:
        <p><strike>Art. 10. Fica revogado...</strike></p>

    Estratégia:
    - Se a tag <strike>/<s> contém um marcador de artigo (Art. N), a revogação
      é explicitada no texto com o prefixo '(REVOGADO)' para que o chunker
      (`ana/rag/ingestao.py`) possa definir vigencia=REVOGADA no chunk.
    - Se o <strike> contém apenas texto de inciso/parágrafo, o conteúdo é
      simplesmente removido (não é a unidade de chunk).
    """
    for tag in soup.find_all(["strike", "s"]):
        texto_interno = tag.get_text(strip=True)
        # Só preserva como revogado se o trecho for um artigo principal
        if _RE_ARTIGO_INICIO.search(texto_interno):
            tag.replace_with(f" {_MARCADOR_REVOGADO} {texto_interno} ")
        else:
            # Inciso/parágrafo revogado: remove silenciosamente
            tag.decompose()


def extrair_texto_planalto(html: str) -> str:
    """Extrai texto limpo do HTML do Planalto preservando estrutura legal.

    Estratégia:
    1. Detecta dispositivos revogados em <strike>/<s> e os anota com
       '(REVOGADO)' para processamento pelo chunker hierárquico.
    2. Remove scripts, estilos e links de navegação.
    3. Tenta extrair parágrafos com classes específicas de artigos.
    4. Fallback: extrai todo o texto do corpo (com limiar de 1 kB).

    Args:
        html: HTML bruto do Planalto.

    Returns:
        Texto com estrutura de artigos preservada. Artigos revogados aparecem
        prefixados com '(REVOGADO)' para que o chunker marque vigencia=REVOGADA.
    """
    _verificar_bs4()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")

    # Detecta revogações ANTES de remover tags
    _marcar_strike(soup)

    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()

    _RE_CLASSE_ARTIGO = re.compile(
        r"(Artigo|artigo|caput|paragrafo|Paragrafo|inciso|alinea|dispositivo)",
        re.IGNORECASE,
    )
    paras_artigo = soup.find_all(["p", "div"], class_=_RE_CLASSE_ARTIGO)

    if paras_artigo:
        linhas = [p.get_text(separator=" ", strip=True) for p in paras_artigo if p.get_text(strip=True)]
    else:
        linhas = []

    # Fallback se classes CSS retornarem texto insuficiente (< 1 kB)
    if sum(len(l) for l in linhas) < 1000:
        corpo = soup.find("body") or soup
        texto_bruto = corpo.get_text(separator="\n", strip=True)
        linhas = [l.strip() for l in texto_bruto.splitlines() if l.strip()]

    texto = "\n".join(linhas)

    artigos_encontrados = len(_RE_ARTIGO_INICIO.findall(texto))
    revogados = texto.count(_MARCADOR_REVOGADO)
    if artigos_encontrados < 3:
        logger.debug(f"Apenas {artigos_encontrados} artigos encontrados no HTML")
    if revogados:
        logger.info(f"Planalto: {revogados} dispositivo(s) marcado(s) como revogado(s)")

    return texto


class ScraperPlanalto(ScraperBase):
    """Scraper para as principais leis federais do planalto.gov.br."""

    def nome(self) -> str:
        return "planalto"

    def _coletar_lei(
        self,
        titulo: str,
        url: str,
        area: str | None,
        data_pub_iso: str | None,
    ) -> DocumentoColetado | None:
        resp = self._http_get(url)
        if resp is None:
            return None

        # Alguns arquivos do Planalto são UTF-16 com BOM (ex: Lei Maria da Penha).
        # httpx os decodifica como latin-1, produzindo texto ilegível.
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
            logger.error(f"Erro ao extrair texto de {titulo}: {e}")
            return None

        if len(texto) < 200:
            logger.warning(f"Texto muito curto para '{titulo}' ({len(texto)} chars) — ignorando")
            return None

        data_publicacao: datetime | None = None
        if data_pub_iso:
            try:
                data_publicacao = datetime.fromisoformat(data_pub_iso)
            except ValueError:
                pass

        logger.info(f"Planalto: coletado '{titulo}' ({len(texto)} chars)")
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
        """Coleta todas as leis prioritárias do Planalto."""
        logger.info(f"Planalto: iniciando coleta de {len(LEIS_PRIORITARIAS)} leis")
        for titulo, (url, area, data_pub) in LEIS_PRIORITARIAS.items():
            doc = self._coletar_lei(titulo, url, area, data_pub)
            if doc:
                yield doc

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta todas as leis (Planalto não tem feed de alterações)."""
        yield from self.coletar()
