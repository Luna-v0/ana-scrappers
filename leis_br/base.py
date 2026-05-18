"""Classe base para scrapers de fontes jurídicas públicas brasileiras.

Define a interface comum e helpers de HTTP com rate limiting, retry e
headers de browser para uso em portais governamentais.
"""

import hashlib
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
from loguru import logger

from leis_br.modelos import DocumentoColetado


# User-Agent de browser — portais governamentais bloqueiam bots (planalto.gov.br, etc.)
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"

_HEADERS_BROWSER = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.5",
}

# Códigos HTTP com falha permanente (não há motivo para retry)
_STATUS_PERMANENTES = {
    400,  # Bad Request
    403,  # Forbidden (Cloudflare/bot-bloqueio)
    404,  # Not Found
    410,  # Gone
}


class ScraperBase(ABC):
    """Interface comum para scrapers de fontes jurídicas.

    Attributes:
        DELAY_ENTRE_REQUISICOES: Pausa mínima entre requisições (segundos).
        MAX_RETRIES: Tentativas antes de desistir de uma URL.
        TIMEOUT: Timeout HTTP em segundos.
        VERIFY_SSL: False para portais com certificados ICP-Brasil não incluídos
            no bundle padrão (ex: STF).
    """

    DELAY_ENTRE_REQUISICOES: float = 1.5
    MAX_RETRIES: int = 3
    TIMEOUT: float = 30.0
    VERIFY_SSL: bool = True

    def __init__(self) -> None:
        self._ultima_requisicao: float = 0.0

    def _http_get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        """GET com rate limiting, retries e headers de browser.

        Falhas permanentes (403, 404, 410) não são re-tentadas.

        Returns:
            Response bem-sucedida ou None em caso de falha permanente/esgotamento.
        """
        decorrido = time.time() - self._ultima_requisicao
        if decorrido < self.DELAY_ENTRE_REQUISICOES:
            time.sleep(self.DELAY_ENTRE_REQUISICOES - decorrido)

        for tentativa in range(self.MAX_RETRIES):
            try:
                with httpx.Client(
                    headers=_HEADERS_BROWSER,
                    timeout=self.TIMEOUT,
                    follow_redirects=True,
                    verify=self.VERIFY_SSL,
                ) as cliente:
                    resp = cliente.get(url, **kwargs)
                    resp.raise_for_status()
                    self._ultima_requisicao = time.time()
                    return resp

            except httpx.HTTPStatusError as e:
                codigo = e.response.status_code
                if codigo in _STATUS_PERMANENTES:
                    logger.warning(f"HTTP {codigo} permanente: {url}")
                    return None
                logger.warning(
                    f"HTTP {codigo} em {url} (tentativa {tentativa + 1}/{self.MAX_RETRIES})"
                )

            except httpx.TimeoutException:
                logger.warning(
                    f"Timeout em {url} (tentativa {tentativa + 1}/{self.MAX_RETRIES})"
                )

            except Exception as e:
                logger.warning(f"Erro inesperado em {url}: {type(e).__name__}: {e}")

            if tentativa < self.MAX_RETRIES - 1:
                time.sleep(2**tentativa)

        logger.error(f"Desistindo de {url} após {self.MAX_RETRIES} tentativas")
        return None

    def _playwright_post_json(
        self,
        base_url: str,
        api_path: str,
        body: dict,
        wait_for: str = "networkidle",
        timeout_ms: int = 45_000,
    ) -> dict | None:
        """Carrega uma página com Playwright (bypassa WAF/Cloudflare) e faz
        POST JSON para um endpoint relativo usando fetch no contexto do browser.

        Útil quando a API requer cookies de challenge (ex: AWS WAF) que
        só o browser headless consegue obter.

        Args:
            base_url: URL da página SPA a carregar (resolve o WAF challenge).
            api_path: Caminho relativo do endpoint JSON (ex: '/api/search/search').
            body: Dicionário a serializar como JSON no body do POST.
            wait_for: Estratégia de espera para o carregamento inicial.
            timeout_ms: Timeout em milissegundos.

        Returns:
            Resposta JSON como dicionário ou None se falhar.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.debug("Playwright não instalado. Execute: pip install leis-br[playwright]")
            return None

        import json as _json

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=_USER_AGENT,
                    locale="pt-BR",
                )
                page = ctx.new_page()
                page.goto(base_url, wait_until=wait_for, timeout=timeout_ms)

                # Execute POST via browser's fetch (reuses WAF cookies)
                result = page.evaluate(
                    """
                    async (req) => {
                        try {
                            const resp = await fetch(req.path, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                                body: JSON.stringify(req.body)
                            });
                            const text = await resp.text();
                            return {ok: resp.ok, status: resp.status, text: text};
                        } catch(e) {
                            return {ok: false, status: 0, text: 'ERR: ' + e.message};
                        }
                    }
                    """,
                    {"path": api_path, "body": body},
                )
                browser.close()

                if not result.get("ok"):
                    logger.warning(
                        f"Playwright POST {api_path}: status={result.get('status')}"
                    )
                    return None
                return _json.loads(result["text"])
        except Exception as e:
            logger.warning(f"Playwright POST {api_path} falhou: {e}")
            return None

    def _playwright_html(
        self,
        url: str,
        wait_for: str = "networkidle",
        timeout_ms: int = 30_000,
    ) -> str | None:
        """Obtém HTML renderizado via Playwright (headless Chromium).

        Útil para portais com JS challenge (WAF, Cloudflare, SPAs).

        Args:
            url: URL a renderizar.
            wait_for: Estratégia de espera ('networkidle', 'load', 'domcontentloaded').
            timeout_ms: Timeout em milissegundos.

        Returns:
            HTML renderizado ou None se Playwright não estiver instalado ou falhar.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.debug("Playwright não instalado. Execute: pip install leis-br[playwright]")
            return None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                ctx = browser.new_context(
                    user_agent=_USER_AGENT,
                    locale="pt-BR",
                    extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9"},
                )
                page = ctx.new_page()
                page.goto(url, wait_until=wait_for, timeout=timeout_ms)
                html = page.content()
                browser.close()
                return html
        except Exception as e:
            logger.warning(f"Playwright falhou em {url}: {e}")
            return None

    @staticmethod
    def _hash(texto: str) -> str:
        """SHA-256 truncado do texto (16 hex chars = 64 bits de entropia)."""
        return hashlib.sha256(texto.encode()).hexdigest()[:16]

    @abstractmethod
    def nome(self) -> str:
        """Identificador único desta fonte (ex: 'planalto', 'stf')."""
        ...

    @abstractmethod
    def coletar(self) -> Iterator[DocumentoColetado]:
        """Coleta todos os documentos desta fonte (gerador lazy).

        Yields:
            DocumentoColetado para cada documento coletado com sucesso.
        """
        ...

    @abstractmethod
    def verificar_atualizacoes(self, desde: datetime) -> Iterator[DocumentoColetado]:
        """Coleta apenas documentos publicados/alterados desde a data.

        Args:
            desde: Limite inferior de data de publicação.

        Yields:
            DocumentoColetado com documentos novos ou alterados.
        """
        ...
