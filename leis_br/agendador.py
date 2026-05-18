"""Agendador de coletas periódicas com APScheduler.

Executa atualizações incrementais automaticamente via AsyncIOScheduler.
Integra com FastAPI via lifespan ou pode ser usado standalone.

No primeiro boot (cache vazio), dispara coleta de todas as fontes
em background automaticamente.

Requer dependência opcional::

    pip install leis-br[scrapers]
"""

import asyncio

from loguru import logger


def _verificar_apscheduler() -> None:
    try:
        import apscheduler  # noqa: F401
    except ImportError:
        raise ImportError(
            "APScheduler não instalado.\n"
            "Execute: pip install leis-br[scrapers]"
        )


# Intervalo de coleta por fonte em horas (padrão: 7 dias)
INTERVALOS_HORAS: dict[str, int] = {
    "planalto": 168,
    "lexml": 168,
    "stf": 168,
    "stj": 168,
    "tst": 168,
}


class AgendadorScrapers:
    """Agenda execuções periódicas do :class:`PipelineScrapers`.

    Usa APScheduler com AsyncIOScheduler para integração com asyncio
    (FastAPI/uvicorn ou qualquer event loop Python).

    Args:
        pipeline: Instância de :class:`~leis_br.pipeline.PipelineScrapers`
            já configurada com seu ingestor. Se ``None``, uma instância
            padrão (sem ingestor externo) é criada ao chamar
            :meth:`iniciar`.

    Example::

        agendador = AgendadorScrapers(pipeline=meu_pipeline)
        agendador.iniciar()
        # ... na parada da app:
        agendador.parar()
    """

    def __init__(self, pipeline=None) -> None:
        self._scheduler = None
        self._pipeline = pipeline

    def iniciar(self) -> None:
        """Inicia o agendador e registra os jobs de coleta periódica."""
        try:
            _verificar_apscheduler()
        except ImportError as e:
            logger.warning(f"Agendador desativado: {e}")
            return

        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        if self._pipeline is None:
            from leis_br.pipeline import PipelineScrapers
            self._pipeline = PipelineScrapers()

        # Bootstrap se o cache estiver vazio
        _fazer_bootstrap = self._pipeline.cache.total() == 0

        self._scheduler = AsyncIOScheduler(timezone="America/Sao_Paulo")

        for fonte, horas in INTERVALOS_HORAS.items():
            self._scheduler.add_job(
                self._executar_atualizacao,
                trigger="interval",
                hours=horas,
                args=[fonte],
                id=f"leis_br_{fonte}",
                name=f"Coleta {fonte}",
                replace_existing=True,
            )
            logger.info(f"Agendador: {fonte} a cada {horas}h")

        self._scheduler.start()
        logger.info("Agendador leis-br iniciado")

        if _fazer_bootstrap:
            asyncio.ensure_future(self._bootstrap())

    async def _bootstrap(self) -> None:
        """Dispara coleta completa de todas as fontes em background."""
        try:
            logger.info("Bootstrap: cache vazio — iniciando coleta inicial em background")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._pipeline.coletar_tudo)
            logger.info("Bootstrap: coleta inicial concluída")
        except Exception as e:
            logger.warning(f"Bootstrap: erro durante coleta inicial (ignorado): {e}")

    async def _executar_atualizacao(self, fonte: str) -> None:
        """Callback do job agendado — executa atualização incremental."""
        if self._pipeline is None:
            return
        logger.info(f"Agendador: atualizando '{fonte}'")
        resultado = self._pipeline.atualizar_incrementalmente(fonte)
        logger.info(
            f"Agendador '{fonte}': {resultado.documentos_novos} novos, "
            f"{len(resultado.erros)} erros"
        )

    def parar(self) -> None:
        """Para o agendador no encerramento da aplicação."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("Agendador leis-br encerrado")

    @property
    def ativo(self) -> bool:
        """True se o agendador está rodando."""
        return self._scheduler is not None and self._scheduler.running
