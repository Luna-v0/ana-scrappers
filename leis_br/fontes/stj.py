"""Scraper para Súmulas do STJ (Superior Tribunal de Justiça).

Estratégia de coleta:
    1. PDF "Inteiro Teor das Súmulas" publicado em processo.stj.jus.br.
       URL: http://processo.stj.jus.br/docs_internet/jurisprudencia/
              tematica/download/SU/Sumulas/SumulasSTJ.pdf
       — não requer autenticação, sem Cloudflare neste caminho.
    2. Extração de texto via pypdf, coluna "Enunciado" de cada súmula.
    3. Súmulas canceladas e revogadas são ignoradas;
       súmulas alteradas são incluídas com o texto atual.

Nota: o portal scon.stj.jus.br está protegido por Cloudflare JS Challenge
(managed challenge) que bloqueia curl e Playwright headless (Chromium e
Firefox). O PDF em processo.stj.jus.br é a única fonte acessível sem
browser interativo.
"""

import io
import re
from collections.abc import Iterator
from datetime import datetime

from loguru import logger

from leis_br.base import ScraperBase
from leis_br.modelos import DocumentoColetado

_URL_PDF = (
    "http://processo.stj.jus.br/docs_internet/jurisprudencia/"
    "tematica/download/SU/Sumulas/SumulasSTJ.pdf"
)

# Padrão para extrair enunciado de cada súmula do texto pypdf.
# A disposição da página é uma tabela com 2 colunas:
#   Coluna esquerda : Enunciado + texto da súmula
#   Coluna direita  : Órgão Julgador + seção + referências
# pypdf lê as colunas em sequência (esquerda inteira, depois direita).
# Formato resultante:
#   SÚMULA N[( SÚMULA ALTERADA)]\n
#   SUBJECT\n
#   Enunciado:\n
#   <texto da súmula>
#   Referências ...  ←  fim do bloco
_RE_SUMULA = re.compile(
    r"S[ÚU]MULA\s+(\d+)(?:\s+\(S[ÚU]MULA\s+ALTERADA\))?\s*\n"
    r"(?:[^\n]+\n)*?"
    r"Enunciado:\s*\n"
    r"([\s\S]{10,800}?)"
    r"(?=\s*(?:Referências|[Óó]rg[ãa]o\s+Julgador|S[ÚU]MULA\s+\d+|\d+\.\s*S[úu]mula))"
)

# Indicadores de súmulas inválidas (canceladas ou revogadas)
_INVALIDAS = ("CANCELADA", "REVOGADA")


def _extrair_sumulas_pdf(pdf_bytes: bytes) -> list[tuple[str, str]]:
    """Extrai tuplas (número, texto) do PDF de súmulas do STJ.

    Args:
        pdf_bytes: Conteúdo binário do PDF.

    Returns:
        Lista de tuplas (número_str, enunciado) ordenada por número,
        excluindo súmulas canceladas e revogadas.
    """
    try:
        import pypdf
    except ImportError:
        raise ImportError(
            "pypdf é necessário para o scraper STJ.\n"
            "Execute: pip install pypdf"
        )

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

    paginas: list[str] = []
    for page in reader.pages:
        txt = page.extract_text()
        if txt:
            paginas.append(txt)

    texto = "\n".join(paginas)
    logger.debug(f"STJ PDF: {len(reader.pages)} páginas, {len(texto)} chars extraídos")

    sumulas: list[tuple[str, str]] = []
    for m in _RE_SUMULA.finditer(texto):
        num = m.group(1)
        # Verificar no cabeçalho se a súmula foi cancelada/revogada
        cabecalho = texto[m.start() : m.start() + 80]
        if any(inv in cabecalho for inv in _INVALIDAS):
            continue
        enunciado = re.sub(r"\s+", " ", m.group(2)).strip()
        if len(enunciado) > 10:
            sumulas.append((num, enunciado))

    # Ordenar por número (inteiro)
    sumulas.sort(key=lambda x: int(x[0]) if x[0].isdigit() else 0)
    return sumulas


class ScraperSTJ(ScraperBase):
    """Scraper para Súmulas do Superior Tribunal de Justiça.

    Coleta o PDF "Inteiro Teor das Súmulas" publicado em processo.stj.jus.br
    (acessível sem autenticação) e extrai os enunciados via pypdf.

    O portal scon.stj.jus.br — que lista as súmulas como HTML — está
    protegido por Cloudflare managed challenge e não é acessível via
    Playwright headless, mesmo com Firefox.
    """

    # PDF do STJ é HTTP (não HTTPS), sem TLS a verificar
    VERIFY_SSL = False
    # PDF grande (~40 MB): timeout maior
    TIMEOUT = 120.0

    def nome(self) -> str:
        return "stj"

    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta súmulas do STJ via PDF publicado em processo.stj.jus.br."""
        logger.info("STJ: iniciando coleta via PDF de súmulas")

        resp = self._http_get(_URL_PDF)
        if resp is None:
            logger.warning(
                "STJ: PDF de súmulas indisponível. "
                f"URL: {_URL_PDF}"
            )
            return

        try:
            sumulas = _extrair_sumulas_pdf(resp.content)
        except ImportError:
            raise
        except Exception as e:
            logger.error(f"STJ: erro ao extrair súmulas do PDF: {e}")
            return

        if not sumulas:
            logger.warning("STJ: nenhuma súmula extraída do PDF")
            return

        logger.info(f"STJ: {len(sumulas)} súmulas extraídas do PDF")

        linhas = [f"Art. {num}º Súmula {num}: {txt}" for num, txt in sumulas]
        texto = "\n".join(linhas)

        yield DocumentoColetado(
            url_origem=_URL_PDF,
            fonte="Súmulas STJ",
            tipo="sumula",
            area=None,
            titulo="Súmulas STJ",
            texto=texto,
            data_publicacao=None,
            data_coleta=datetime.now(),
            hash_conteudo=self._hash(texto),
            orgao="STJ",
        )

    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Recoleta todas as súmulas (STJ não oferece feed incremental)."""
        yield from self.coletar()
