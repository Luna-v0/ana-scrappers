"""Cache SQLite para controle de documentos coletados.

Evita recoleta de documentos sem alterações desde a última execução,
usando hash SHA-256 do conteúdo como chave de deduplicação.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger


class CacheScrapers:
    """Controla quais URLs já foram coletadas e seus hashes de conteúdo.

    Attributes:
        caminho_db: Caminho para o arquivo SQLite do cache.
    """

    def __init__(self, caminho_db: Path) -> None:
        self.caminho_db = caminho_db
        caminho_db.parent.mkdir(parents=True, exist_ok=True)
        self._inicializar()

    def _inicializar(self) -> None:
        with sqlite3.connect(self.caminho_db) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS documentos (
                    url         TEXT PRIMARY KEY,
                    hash        TEXT NOT NULL,
                    fonte       TEXT NOT NULL,
                    titulo      TEXT NOT NULL DEFAULT '',
                    vigencia    TEXT NOT NULL DEFAULT 'ativa',
                    data_coleta TEXT NOT NULL
                )
            """)
            conn.commit()

    def ja_coletado(self, url: str, hash_conteudo: str) -> bool:
        """Verifica se um documento já foi coletado com o mesmo hash."""
        with sqlite3.connect(self.caminho_db) as conn:
            row = conn.execute(
                "SELECT hash FROM documentos WHERE url = ?", (url,)
            ).fetchone()
        return row is not None and row[0] == hash_conteudo

    def registrar(
        self,
        url: str,
        hash_conteudo: str,
        fonte: str,
        titulo: str = "",
        vigencia: str = "ativa",
    ) -> None:
        """Registra ou atualiza um documento no cache."""
        with sqlite3.connect(self.caminho_db) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO documentos
                    (url, hash, fonte, titulo, vigencia, data_coleta)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (url, hash_conteudo, fonte, titulo, vigencia, datetime.now().isoformat()),
            )
            conn.commit()
        logger.debug(f"Cache: registrado '{titulo}' de '{fonte}'")

    def ultima_coleta(self, fonte: str) -> datetime | None:
        """Retorna o timestamp da última coleta bem-sucedida de uma fonte."""
        with sqlite3.connect(self.caminho_db) as conn:
            row = conn.execute(
                "SELECT MAX(data_coleta) FROM documentos WHERE fonte = ?", (fonte,)
            ).fetchone()
        if row and row[0]:
            return datetime.fromisoformat(row[0])
        return None

    def total(self, fonte: str | None = None) -> int:
        """Conta documentos no cache, opcionalmente por fonte."""
        with sqlite3.connect(self.caminho_db) as conn:
            if fonte:
                return conn.execute(
                    "SELECT COUNT(*) FROM documentos WHERE fonte = ?", (fonte,)
                ).fetchone()[0]
            return conn.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]

    def listar(self) -> list[dict]:
        """Lista todos os documentos no cache."""
        with sqlite3.connect(self.caminho_db) as conn:
            rows = conn.execute(
                "SELECT url, hash, fonte, titulo, vigencia, data_coleta "
                "FROM documentos ORDER BY data_coleta DESC"
            ).fetchall()
        return [
            {"url": r[0], "hash": r[1], "fonte": r[2],
             "titulo": r[3], "vigencia": r[4], "data_coleta": r[5]}
            for r in rows
        ]
