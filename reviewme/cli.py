"""Entrée one-shot : `reviewme review --repo OWNER/REPO --pr N`.

C'est la brique commune (« une PR, une review, puis on sort »), réutilisée par le VPS
(appelée en boucle/cron), par un webhook, ou par un step CI (Bitrise / GitHub Actions).

Commandes annexes :
  `reviewme init-project <repo>`  crée `config/projects/<repo>/` depuis le squelette
  `reviewme projects`             liste les projets et leurs reviewers
"""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import sys
from pathlib import Path

from .config import load_config
from .logging_ import setup_logging
from .projects import PROJECTS_DIR as PROJECTS_DIR_DEFAULT
from .projects import TEMPLATES_DIR, ProjectConfigError, load_project, projects_dir
from .run import run_review_by_number


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="reviewme", description="Review one-shot d'une PR GitHub")
    sub = parser.add_subparsers(dest="command")

    rv = sub.add_parser("review", help="Review une PR précise")
    for p in (parser, rv):  # accepte `reviewme --repo .. --pr ..` et `reviewme review --repo .. --pr ..`
        p.add_argument("--repo", help="OWNER/REPO (sinon GITHUB_REPO du .env)")
        p.add_argument("--pr", type=int, help="Numéro de la PR")
        p.add_argument("--force", action="store_true", help="Re-review même si déjà fait pour ce commit")
        p.add_argument("--dry-run", action="store_true", help="Ne poste rien, log seulement")

    ip = sub.add_parser("init-project", help="Crée un dossier de projet depuis le squelette")
    ip.add_argument("name", help="Nom du dossier = nom du REPO (ex. ah-ios), jamais celui de l'équipe")
    ip.add_argument("--reviewers", default="tech",
                    help="Reviewers à copier, séparés par des virgules (défaut : tech)")

    sub.add_parser("projects", help="Liste les projets configurés et leurs reviewers")

    fb = sub.add_parser("feedback",
                        help="Retours de développeurs sur une PR, en règles à relire")
    fb.add_argument("--pr", type=int, required=True, help="Numéro de la PR")
    fb.add_argument("--repo", dest="fb_repo", help="OWNER/REPO (sinon GITHUB_REPO)")

    st = sub.add_parser("stats", help="Statistiques agrégées (compteurs, sans contenu)")
    st.add_argument("--html", metavar="FICHIER",
                    help="Écrit un rapport HTML autonome au lieu d'afficher le résumé")
    st.add_argument("--json", action="store_true", help="Sortie JSON brute")
    return parser.parse_args(argv)


def _init_project(name: str, reviewer_ids: list[str]) -> int:
    """Copie le squelette. Copie à la création, pas d'héritage au runtime (ADR v3 D1)."""
    root = projects_dir()
    target = root / name
    if target.exists():
        print(f"Erreur : {target} existe déjà.", file=sys.stderr)
        return 1
    if not TEMPLATES_DIR.is_dir():
        print(f"Erreur : squelette introuvable ({TEMPLATES_DIR}).", file=sys.stderr)
        return 1

    missing = [r for r in reviewer_ids if not (TEMPLATES_DIR / "reviewers" / r).is_dir()]
    if missing:
        available = sorted(p.name for p in (TEMPLATES_DIR / "reviewers").iterdir() if p.is_dir())
        print(f"Erreur : reviewer(s) inconnu(s) {missing}. Disponibles : {', '.join(available)}",
              file=sys.stderr)
        return 1

    (target / "reviewers").mkdir(parents=True)
    if (TEMPLATES_DIR / "common").is_dir():
        shutil.copytree(TEMPLATES_DIR / "common", target / "common")
    else:
        (target / "common").mkdir()
    for rid in reviewer_ids:
        shutil.copytree(TEMPLATES_DIR / "reviewers" / rid, target / "reviewers" / rid)

    print(f"Projet créé : {target}")
    if root != PROJECTS_DIR_DEFAULT:
        print(f"  (dépôt de config externe : {root.parent})")
    print(f"  reviewers : {', '.join(reviewer_ids)}")
    print("  À faire ensuite :")
    print(f"    1. ajuster les consignes communes dans {target / 'common'}/*.md")
    print(f"    2. adapter les personas dans {target / 'reviewers'}/<id>/system.md")
    print("       (les conventions de code, l'agent les lit dans le dépôt reviewé — ne pas les recopier)")
    print(f"    3. activer le projet : PROJECT={name} dans le .env")
    print("  (ajouter un reviewer plus tard = créer son dossier dans reviewers/ ; le mettre en")
    print("   sommeil = `enabled = false` dans son reviewer.toml)")
    return 0


def _list_projects() -> int:
    root = projects_dir()
    if not root.is_dir():
        print(f"Aucun projet configuré ({root} n'existe pas).")
        print("Créer le premier : reviewme init-project <repo> --reviewers tech,us")
        return 0
    names = sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not names:
        print("Aucun projet configuré.")
        return 0
    for name in names:
        try:
            project = load_project(name, root)
        except ProjectConfigError as e:
            print(f"{name} : ⚠️  {e}")
            continue
        for spec in sorted(project.reviewers, key=lambda r: (r.priority, r.id)):
            when = f", quand {list(spec.when_paths)}" if spec.when_paths else ""
            needs = f", exige {list(spec.requires)}" if spec.requires else ""
            print(f"{name} · {spec.id} ({spec.output_mode}, priorité {spec.priority}{when}{needs})")
    return 0


def _feedback(args) -> int:
    import dataclasses as _dc

    from .feedback import collecter, en_regles
    from .github_client import GitHubClient
    from .logging_ import setup_logging

    config = load_config(require_repo=False)
    if args.fb_repo:
        config = _dc.replace(config, github_repo=args.fb_repo)
    if not config.github_repo:
        print("Erreur : --repo requis (ou GITHUB_REPO dans .env)", file=sys.stderr)
        return 2

    logger = setup_logging()
    gh = GitHubClient(config)
    try:
        echanges = collecter(gh, args.pr, logger)
    finally:
        gh.close()

    if not echanges:
        print(f"PR #{args.pr} : aucune remarque n'a reçu de réponse.")
        return 0

    print(f"PR #{args.pr} : {len(echanges)} réponse(s) de développeurs.\n")
    print(en_regles(echanges, args.pr))
    print("\n---\nÀ relire, puis coller dans `common/regles-terrain.md` du projet si la "
          "règle vaut au-delà de cette PR.")
    return 0


def _stats(args) -> int:
    import json

    from .report import ecrire
    from .stats import resume

    if args.html:
        chemin = ecrire(Path(args.html))
        print(f"Rapport écrit : {chemin}")
        return 0

    r = resume()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    if not r.get("runs"):
        print("Aucune review enregistrée.")
        return 0
    print(f"{r['runs']} run(s) sur {r['prs']} PR")
    print(f"  remarques postées      : {r['remarques_postees']}")
    print(f"  évitées (dédup+seuil)  : {r['remarques_dedupliquees'] + r['remarques_sous_seuil']}")
    print(f"  coût total             : {r['cout_total_usd']:.2f} $")
    print(f"  coût moyen par PR      : {r['cout_moyen_par_pr']:.2f} $")
    return 0


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args.command == "init-project":
        raise SystemExit(_init_project(args.name, [r.strip() for r in args.reviewers.split(",") if r.strip()]))
    if args.command == "projects":
        raise SystemExit(_list_projects())
    if args.command == "stats":
        raise SystemExit(_stats(args))
    if args.command == "feedback":
        raise SystemExit(_feedback(args))

    if args.pr is None:
        print("Usage : reviewme review --repo OWNER/REPO --pr N [--dry-run] [--force]", file=sys.stderr)
        print("        reviewme init-project <repo> [--reviewers tech,us,i18n]", file=sys.stderr)
        print("        reviewme projects", file=sys.stderr)
        raise SystemExit(2)

    config = load_config(require_repo=False)
    overrides: dict = {"dry_run": args.dry_run or config.dry_run}
    if args.repo:
        overrides["github_repo"] = args.repo
    if not (args.repo or config.github_repo):
        print("Erreur : --repo requis (ou GITHUB_REPO dans .env)", file=sys.stderr)
        raise SystemExit(2)
    config = dataclasses.replace(config, **overrides)

    logger = setup_logging()
    logger.info("ReviewMe one-shot : %s PR #%s%s",
                config.github_repo, args.pr, " [DRY-RUN]" if config.dry_run else "")
    result = run_review_by_number(args.pr, config, logger, force=args.force)
    if result.get("error"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
