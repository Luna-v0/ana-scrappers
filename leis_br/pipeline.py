"""Pipeline de orquestração: scrapers → cache → ingestor.

O pipeline gerencia descoberta de fontes, deduplicação por hash e
delegação do processamento ao callback ``ingestor``.

O callback recebe um :class:`DocumentoColetado` e retorna o número de
unidades processadas (≥1 = novo, 0 = ignorado/falha). Isso separa o
scraping do stack de ML/indexação, permitindo que consumidores como o
ANA injetem sua própria lógica de chunking + embeddings + indexação.

Exemplo de uso standalone (coleta apenas, sem indexação)::

    from leis_br import PipelineScrapers

    pipeline = PipelineScrapers()
    resultado = pipeline.coletar_fonte("planalto")
    print(resultado.documentos_novos)

Exemplo com ingestor customizado::

    from leis_br import PipelineScrapers, DocumentoColetado

    def meu_ingestor(doc: DocumentoColetado) -> int:
        # chunkar, embeddar, indexar...
        return n_chunks_indexados

    pipeline = PipelineScrapers(ingestor=meu_ingestor)
    pipeline.coletar_tudo()
"""

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from loguru import logger

from leis_br.cache import CacheScrapers
from leis_br.modelos import DocumentoColetado, ResultadoColeta

# Tipo do callback de ingestão
IngestorType = Callable[[DocumentoColetado], int]


@runtime_checkable
class CacheProtocol(Protocol):
    """Interface mínima que qualquer backend de cache deve satisfazer."""

    def ja_coletado(self, url: str, hash_conteudo: str) -> bool: ...
    def registrar(
        self,
        url: str,
        hash_conteudo: str,
        fonte: str,
        titulo: str,
        vigencia: str,
    ) -> None: ...
    def ultima_coleta(self, fonte: str) -> datetime | None: ...
    def total(self, fonte: str | None = None) -> int: ...


def _caminho_sqlite_padrao() -> Path:
    if os.path.exists("/.dockerenv"):
        return Path("/app/data/leis_br_cache.db")
    return Path.home() / ".local" / "share" / "leis_br" / "cache.db"


def _caminho_log_padrao() -> Path:
    if os.path.exists("/.dockerenv"):
        return Path("/app/data/leis_br_update_log.json")
    return Path.home() / ".local" / "share" / "leis_br" / "update_log.json"


def _criar_fontes() -> dict:
    from leis_br.fontes.planalto import ScraperPlanalto
    from leis_br.fontes.lexml import ScraperLexML
    from leis_br.fontes.stf import ScraperSTF
    from leis_br.fontes.stj import ScraperSTJ
    from leis_br.fontes.tst import ScraperTST
    from leis_br.fontes.alerj import ScraperALERJ

    return {
        "planalto": ScraperPlanalto(),
        "lexml": ScraperLexML(),
        "stf": ScraperSTF(),
        "stj": ScraperSTJ(),
        "tst": ScraperTST(),
        "alerj": ScraperALERJ(),
    }


class PipelineScrapers:
    """Orquestra a coleta de documentos jurídicos via scrapers.

    Gerencia descoberta de fontes, caching por hash e deduplicação.
    O processamento efetivo (chunking, embeddings, indexação) é delegado
    ao ``ingestor``, mantendo o pacote livre de dependências de ML.

    Args:
        ingestor: Callable que recebe um :class:`DocumentoColetado` e retorna
            o número de unidades processadas (0 = ignorado). Se ``None``,
            documentos são coletados e registrados no cache sem processamento
            adicional — útil para testes ou coleta pura.
        cache: Implementação do :class:`CacheProtocol`. Padrão: SQLite local.
        caminho_log: Destino do JSON de log de atualizações.
    """

    def __init__(
        self,
        *,
        ingestor: IngestorType | None = None,
        cache: CacheProtocol | None = None,
        caminho_log: Path | None = None,
    ) -> None:
        self.cache: CacheProtocol = cache or CacheScrapers(_caminho_sqlite_padrao())
        self._ingestor: IngestorType = ingestor or (lambda _doc: 1)
        self._caminho_log = caminho_log or _caminho_log_padrao()
        self._fontes = _criar_fontes()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _processar_documento(self, doc: DocumentoColetado) -> int:
        """Verifica cache e chama ingestor se o documento for novo."""
        if self.cache.ja_coletado(doc.url_origem, doc.hash_conteudo):
            logger.debug(f"Cache hit: '{doc.titulo}' — sem alterações")
            return 0

        n = self._ingestor(doc)

        if n > 0:
            self.cache.registrar(
                url=doc.url_origem,
                hash_conteudo=doc.hash_conteudo,
                fonte=self._resolver_nome_fonte(doc),
                titulo=doc.titulo,
                vigencia=doc.vigencia,
            )

        return n

    def _resolver_nome_fonte(self, doc: DocumentoColetado) -> str:
        url = doc.url_origem.lower()
        for nome in self._fontes:
            if nome in url:
                return nome
        return "outro"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def fontes(self) -> list[str]:
        """Nomes das fontes disponíveis."""
        return list(self._fontes)

    def exportar_log_atualizacoes(self, caminho: Path | None = None) -> None:
        """Serializa timestamps de última coleta por fonte em JSON."""
        alvo = caminho or self._caminho_log
        try:
            log: dict[str, str | None] = {}
            for nome in self._fontes:
                ultima = self.cache.ultima_coleta(nome)
                log[nome] = ultima.isoformat() if ultima else None
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with open(alvo, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False, indent=2)
            logger.debug(f"Log de atualizações exportado: {alvo}")
        except Exception as e:
            logger.warning(f"Erro ao exportar log: {e}")

    def coletar_fonte(self, nome: str) -> ResultadoColeta:
        """Executa a coleta completa de uma fonte específica.

        Args:
            nome: Nome da fonte (ex: 'planalto', 'lexml', 'stf').

        Returns:
            :class:`ResultadoColeta` com estatísticas da execução.

        Raises:
            ValueError: Se o nome da fonte não for reconhecido.
        """
        if nome not in self._fontes:
            raise ValueError(
                f"Fonte '{nome}' desconhecida. Disponíveis: {self.fontes}"
            )

        resultado = ResultadoColeta(fonte=nome)
        scraper = self._fontes[nome]

        logger.info(f"Pipeline: coletando fonte '{nome}'")
        try:
            for doc in scraper.coletar():
                try:
                    n = self._processar_documento(doc)
                    if n > 0:
                        resultado.documentos_novos += 1
                    else:
                        resultado.documentos_ignorados += 1
                except Exception as e:
                    msg = f"Erro ao processar '{doc.titulo}': {e}"
                    logger.error(msg)
                    resultado.erros.append(msg)
        except ImportError as e:
            resultado.erros.append(str(e))
            logger.error(f"Dependências faltando para '{nome}': {e}")
        except Exception as e:
            resultado.erros.append(str(e))
            logger.error(f"Erro inesperado coletando '{nome}': {e}")
        finally:
            resultado.finalizar()
            self.exportar_log_atualizacoes()

        logger.info(
            f"Pipeline '{nome}': {resultado.documentos_novos} novos, "
            f"{resultado.documentos_ignorados} ignorados, "
            f"{len(resultado.erros)} erros — {resultado.duracao_segundos:.1f}s"
        )
        return resultado

    def atualizar_incrementalmente(self, nome: str) -> ResultadoColeta:
        """Coleta apenas documentos alterados desde a última coleta.

        Args:
            nome: Nome da fonte.

        Returns:
            :class:`ResultadoColeta` com estatísticas.
        """
        if nome not in self._fontes:
            raise ValueError(f"Fonte '{nome}' desconhecida.")

        resultado = ResultadoColeta(fonte=nome)
        scraper = self._fontes[nome]
        desde = self.cache.ultima_coleta(nome) or datetime(2000, 1, 1)

        logger.info(f"Pipeline: atualizações de '{nome}' desde {desde.date()}")
        try:
            for doc in scraper.verificar_atualizacoes(desde):
                try:
                    n = self._processar_documento(doc)
                    if n > 0:
                        resultado.documentos_novos += 1
                    else:
                        resultado.documentos_ignorados += 1
                except Exception as e:
                    msg = f"Erro ao processar '{doc.titulo}': {e}"
                    logger.error(msg)
                    resultado.erros.append(msg)
        except Exception as e:
            resultado.erros.append(str(e))
            logger.error(f"Erro ao atualizar '{nome}': {e}")
        finally:
            resultado.finalizar()
            self.exportar_log_atualizacoes()

        return resultado

    def coletar_tudo(self) -> list[ResultadoColeta]:
        """Executa coleta completa de todas as fontes configuradas."""
        logger.info(f"Pipeline: coletando todas as {len(self._fontes)} fontes")
        return [self.coletar_fonte(nome) for nome in self._fontes]

    def status(self) -> dict:
        """Retorna status atual das fontes e do cache."""
        fontes_info = {}
        for nome in self._fontes:
            ultima = self.cache.ultima_coleta(nome)
            fontes_info[nome] = {
                "documentos_no_cache": self.cache.total(nome),
                "ultima_coleta": ultima.isoformat() if ultima else None,
            }
        return {
            "fontes": fontes_info,
            "total_documentos_cache": self.cache.total(),
        }
