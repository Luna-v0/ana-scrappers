"""Modelos de dados para scrapers de legislação brasileira."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DocumentoColetado:
    """Documento jurídico coletado de fonte pública.

    Attributes:
        url_origem: URL canônica do documento.
        fonte: Nome identificador da fonte (ex: 'LGPD (Lei 13.709/2018)').
        tipo: Tipo do documento em string (ex: 'lei_federal', 'sumula').
        area: Área jurídica em string (ex: 'civil', 'penal') ou None.
        titulo: Título humano do documento.
        texto: Conteúdo textual extraído.
        data_publicacao: Data de publicação original.
        data_coleta: Timestamp da coleta.
        vigencia: Status de vigência ('ativa', 'revogada', 'parcialmente_revogada').
        numero_lei: Número da lei no formato brasileiro (ex: 'Lei nº 13.709/2018').
        hash_conteudo: SHA-256 truncado para detectar alterações.
        orgao: Órgão emissor (ex: 'Congresso Nacional', 'STF', 'STJ').
    """

    url_origem: str
    fonte: str
    tipo: str
    area: str | None
    titulo: str
    texto: str
    data_publicacao: datetime | None
    data_coleta: datetime
    vigencia: str = "ativa"
    numero_lei: str = ""
    hash_conteudo: str = ""
    orgao: str = ""


@dataclass
class ResultadoColeta:
    """Resultado de uma execução de coleta de uma fonte.

    Attributes:
        fonte: Nome da fonte coletada.
        documentos_novos: Quantidade de documentos processados pelo ingestor.
        documentos_ignorados: Documentos sem alteração (hash igual ao cache).
        erros: Lista de erros encontrados durante a coleta.
        iniciou_em: Timestamp de início.
        finalizou_em: Timestamp de fim.
        duracao_segundos: Duração total em segundos.
    """

    fonte: str
    documentos_novos: int = 0
    documentos_ignorados: int = 0
    erros: list[str] = field(default_factory=list)
    iniciou_em: datetime = field(default_factory=datetime.now)
    finalizou_em: datetime | None = None
    duracao_segundos: float = 0.0

    def finalizar(self) -> None:
        """Registra o fim da coleta e calcula duração."""
        self.finalizou_em = datetime.now()
        self.duracao_segundos = (self.finalizou_em - self.iniciou_em).total_seconds()
