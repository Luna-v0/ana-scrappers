"""CLI standalone do leis-br.

Uso::

    python -m leis_br [--fonte NOME] [--force] [--status]

    # ou via entry point:
    leis-br [--fonte NOME] [--force] [--status]

Por padrão coleta fontes desatualizadas há mais de 7 dias.
Sem ingestor configurado, apenas registra os documentos no cache local.
"""

import sys
from datetime import datetime, timedelta


def _dependencias_ok() -> bool:
    try:
        import bs4  # noqa: F401
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def executar_cli() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="leis-br",
        description=(
            "Coleta legislação pública brasileira.\n"
            "Por padrão, só executa fontes com mais de 7 dias sem atualização."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Força a coleta agora.")
    parser.add_argument(
        "--fonte",
        metavar="NOME",
        help="Coleta apenas esta fonte (planalto, lexml, stf, stj, tst).",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Mostra o status das fontes e sai sem coletar.",
    )
    args = parser.parse_args()

    if not _dependencias_ok():
        print(
            "❌ Dependências de scraping não instaladas.\n"
            "   Execute: pip install leis-br[scrapers]",
            file=sys.stderr,
        )
        sys.exit(1)

    from leis_br.pipeline import PipelineScrapers

    pipeline = PipelineScrapers()
    agora = datetime.now()
    intervalo = timedelta(days=7)

    st = pipeline.status()
    fontes_info = st["fontes"]

    print("\n📚 Status das fontes:\n")
    print(f"  {'Fonte':<12} {'Documentos':>12}  {'Última coleta':<22}  {'Situação'}")
    print(f"  {'-'*12}  {'-'*12}  {'-'*22}  {'-'*20}")
    for nome, info in fontes_info.items():
        ultima_str = info.get("ultima_coleta") or "nunca"
        n_docs = info.get("documentos_no_cache", 0)
        ultima_dt = None
        if info.get("ultima_coleta"):
            ultima_dt = datetime.fromisoformat(info["ultima_coleta"])
            dias = (agora - ultima_dt).days
            situacao = f"✅ ok ({dias}d atrás)" if dias < 7 else f"⚠️  desatualizado ({dias}d)"
        else:
            situacao = "🔴 nunca coletado"
        print(f"  {nome:<12}  {n_docs:>12}  {ultima_str[:22]:<22}  {situacao}")
    print()

    if args.status:
        return

    todas_as_fontes = list(fontes_info.keys())

    if args.fonte:
        if args.fonte not in todas_as_fontes:
            print(
                f"❌ Fonte '{args.fonte}' desconhecida. "
                f"Disponíveis: {', '.join(todas_as_fontes)}",
                file=sys.stderr,
            )
            sys.exit(1)
        fontes_para_coletar = [args.fonte]
    elif args.force:
        fontes_para_coletar = todas_as_fontes
    else:
        fontes_para_coletar = []
        for nome, info in fontes_info.items():
            ultima_dt = None
            if info.get("ultima_coleta"):
                ultima_dt = datetime.fromisoformat(info["ultima_coleta"])
            if ultima_dt is None or (agora - ultima_dt) > intervalo:
                fontes_para_coletar.append(nome)

    if not fontes_para_coletar:
        print("✅ Todas as fontes foram atualizadas nos últimos 7 dias.")
        print("   Use --force para forçar a recoleta.\n")
        return

    print(f"🔄 Coletando: {', '.join(fontes_para_coletar)}\n")

    total_novos = 0
    total_erros = 0
    for nome in fontes_para_coletar:
        print(f"  ⬇️  {nome}...", end=" ", flush=True)
        resultado = pipeline.coletar_fonte(nome)
        total_novos += resultado.documentos_novos
        total_erros += len(resultado.erros)
        print(
            f"{resultado.documentos_novos} novos, "
            f"{resultado.documentos_ignorados} iguais, "
            f"{len(resultado.erros)} erros "
            f"({resultado.duracao_segundos:.1f}s)"
        )
        for erro in resultado.erros:
            print(f"     ⚠️  {erro}")

    print(f"\n✅ Concluído — {total_novos} documentos coletados, {total_erros} erros.\n")


if __name__ == "__main__":
    executar_cli()
