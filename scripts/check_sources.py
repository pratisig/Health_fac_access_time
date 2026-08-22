#!/usr/bin/env python3
"""Diagnostic des sources distantes utilisées par l'application.

Teste, depuis la machine où tourne l'application, l'accès réel aux trois
ressources OpenAccessLens et au raster WorldPop, et affiche le code HTTP ainsi
que les en-têtes déterminants (CORS, Range, type MIME).

    python scripts/check_sources.py --iso3 sen --category hospitals --year 2020

Utile lorsque la carte du mode 1 reste bloquée : ce script distingue une
ressource absente (404), un blocage réseau, un proxy qui ne répond pas, et un
serveur qui refuse les requêtes Range dont PMTiles a besoin.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import HEIGIT_COUNTRIES_URL  # noqa: E402
from src.data_catalog import pmtiles_url, stats_url  # noqa: E402
from src.population import WORLDPOP_PRODUCTS  # noqa: E402

GREEN, RED, YELLOW, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def check(label: str, url: str, *, ranged: bool = False, timeout: int = 30) -> bool:
    """Teste une URL et décrit précisément le résultat."""
    print(f"\n{label}\n  {url}")
    headers = {"Range": "bytes=0-1023"} if ranged else {}

    try:
        response = requests.get(url, headers=headers, timeout=timeout, stream=True)
    except requests.exceptions.SSLError as error:
        print(f"  {RED}ÉCHEC TLS{RESET} — {error}")
        print("  → proxy d'inspection TLS, pare-feu, ou certificat manquant.")
        return False
    except requests.exceptions.ConnectTimeout:
        print(f"  {RED}DÉLAI DE CONNEXION DÉPASSÉ{RESET}")
        print("  → le serveur ne répond pas : c'est ce cas qui figeait la carte.")
        return False
    except requests.exceptions.ReadTimeout:
        print(f"  {RED}DÉLAI DE LECTURE DÉPASSÉ{RESET}")
        return False
    except requests.RequestException as error:
        print(f"  {RED}INJOIGNABLE{RESET} — {type(error).__name__}: {error}")
        return False

    status = response.status_code
    colour = GREEN if status < 400 else RED
    print(f"  {colour}HTTP {status}{RESET}")

    if ranged:
        if status == 206:
            print(f"  {GREEN}Requêtes Range acceptées{RESET} (indispensable aux PMTiles)")
        elif status == 200:
            print(f"  {YELLOW}Requêtes Range ignorées{RESET} : le navigateur devra tout "
                  "télécharger, ce qui peut sembler bloqué sur une archive volumineuse.")

    for header in ("Content-Type", "Content-Length", "Access-Control-Allow-Origin",
                   "Accept-Ranges"):
        value = response.headers.get(header)
        if value:
            print(f"  {header}: {value}")

    if status < 400 and not response.headers.get("Access-Control-Allow-Origin"):
        print(f"  {YELLOW}Aucun en-tête CORS{RESET} : lisible en Python, mais un "
              "navigateur peut refuser la requête depuis la carte.")

    if status == 404:
        print("  → ressource non publiée pour ce pays ou cette catégorie.")

    response.close()
    return status < 400


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--iso3", default="sen", help="code ISO3 du pays (défaut : sen)")
    parser.add_argument("--category", default="hospitals",
                        choices=["hospitals", "primary_healthcare"])
    parser.add_argument("--year", type=int, default=2020, help="année WorldPop")
    parser.add_argument("--product", default="unconstrained_1km",
                        choices=list(WORLDPOP_PRODUCTS))
    arguments = parser.parse_args()

    iso3 = arguments.iso3.lower()
    print("=" * 72)
    print(f"Diagnostic des sources — {iso3.upper()} / {arguments.category}")
    print("=" * 72)

    results = {
        "Catalogue OpenAccessLens": check("1. Catalogue des pays", HEIGIT_COUNTRIES_URL),
        "Isochrones PMTiles": check("2. Isochrones PMTiles (mode 1)",
                                    pmtiles_url(iso3, arguments.category), ranged=True),
        "Statistiques Parquet": check("3. Statistiques démographiques",
                                      stats_url(iso3, arguments.category), ranged=True),
    }

    product = WORLDPOP_PRODUCTS[arguments.product]
    worldpop_ok = False
    for url in product.urls(iso3, arguments.year):
        if check(f"4. WorldPop — {product.label}", url, ranged=True):
            worldpop_ok = True
            break
    results["Raster WorldPop"] = worldpop_ok

    print("\n" + "=" * 72)
    for label, ok in results.items():
        print(f"  {GREEN + 'OK    ' + RESET if ok else RED + 'ÉCHEC ' + RESET} {label}")
    print("=" * 72)

    if all(results.values()):
        print("\nToutes les sources répondent. Si la carte reste vide, ouvrez la console "
              "du navigateur : le composant affiche désormais l'étape bloquée.")
        return 0

    print("\nAu moins une source est inaccessible depuis cette machine. "
          "L'application le signalera au lieu d'afficher des données inventées.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
