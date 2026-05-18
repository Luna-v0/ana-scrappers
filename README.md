# ana-scrappers

Scrapers de **legislação e jurisprudência brasileira**, consumidos pelo pipeline RAG do [`ana-backend`](https://github.com/Luna-v0/ana-backend).

Pacote Python: `leis_br`.

## Fontes

| Fonte | Módulo | Conteúdo |
|-------|--------|----------|
| Planalto | `leis_br.fontes.planalto` | Constituição, códigos e leis prioritárias |
| LexML / Senado | `leis_br.fontes.lexml` | Normas federais via API do Senado |
| STF | `leis_br.fontes.stf` | Jurisprudência do Supremo |
| STJ | `leis_br.fontes.stj` | Jurisprudência do Superior Tribunal de Justiça |
| TST | `leis_br.fontes.tst` | Jurisprudência do Tribunal Superior do Trabalho |

## Layout

```
leis_br/
├── fontes/        # um módulo por fonte (planalto, lexml, stf, stj, tst)
├── base.py        # interface comum entre scrapers
├── cache.py       # cache local de respostas
├── modelos.py     # tipos / schemas
├── pipeline.py    # orquestra coleta + normalização
├── agendador.py   # agendamento periódico (APScheduler)
└── __main__.py    # `python -m leis_br ...`
```

## Instalação

```bash
uv sync                              # dependências base
uv sync --extra scrapers             # + BeautifulSoup, lxml, APScheduler
uv sync --extra playwright           # + Playwright (para SPAs / WAF)
uv sync --extra all                  # tudo
```

## Uso

```bash
uv run python -m leis_br --fonte planalto   # roda um scraper específico
uv run python -m leis_br --help
```

## Status

Planalto está estável. As fontes de jurisprudência (STF, STJ, TST) tentam APIs REST quando disponíveis e fazem fallback para portais legados; algumas estão sujeitas a WAF e ao uso de SPAs (React), o que pode exigir Playwright.

## Requisitos

Python ≥ 3.12, gerenciado via [uv](https://docs.astral.sh/uv/).
