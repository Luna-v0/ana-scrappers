"""leis-br — Scrapers de legislação e jurisprudência brasileira.

Interface pública::

    from leis_br import PipelineScrapers, DocumentoColetado, ResultadoColeta
    from leis_br import CacheScrapers, CacheProtocol, AgendadorScrapers
    from leis_br.fontes.planalto import ScraperPlanalto, LEIS_PRIORITARIAS

Uso básico (apenas coleta, sem indexação)::

    pipeline = PipelineScrapers()
    resultado = pipeline.coletar_fonte("planalto")

Com ingestor customizado::

    def meu_ingestor(doc: DocumentoColetado) -> int:
        print(doc.titulo, len(doc.texto))
        return 1  # sinaliza que processou

    pipeline = PipelineScrapers(ingestor=meu_ingestor)
    pipeline.coletar_tudo()
"""

from leis_br.agendador import AgendadorScrapers
from leis_br.cache import CacheScrapers
from leis_br.modelos import DocumentoColetado, ResultadoColeta
from leis_br.pipeline import CacheProtocol, IngestorType, PipelineScrapers

__all__ = [
    "AgendadorScrapers",
    "CacheProtocol",
    "CacheScrapers",
    "DocumentoColetado",
    "IngestorType",
    "PipelineScrapers",
    "ResultadoColeta",
]
