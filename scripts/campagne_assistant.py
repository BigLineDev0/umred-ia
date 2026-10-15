"""
Lanceur de la campagne de tests de l'assistant (docs/ASSISTANT_IA_TESTS.md).

    # règles seules (rapide, sans modèle)
    venv/bin/python scripts/campagne_assistant.py resultats.json
    # modèle Qwen réel chargé une fois, 3 passes, avec mesures de performance
    HF_HUB_OFFLINE=1 venv/bin/python scripts/campagne_assistant.py resultats.json --mode qwen --passes 3 --performance
    # autre version du code (comparaison avant / après)
    venv/bin/python scripts/campagne_assistant.py avant.json --code /chemin/copie

Les scénarios et le faux Django viennent TOUJOURS de ce dépôt
(tests/campagne/) ; seul le code de l'assistant (app/, main.py) change avec --code.
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("sortie")
parser.add_argument("--mode", choices=["regles", "qwen"], default="regles")
parser.add_argument("--passes", type=int, default=1)
parser.add_argument("--code", default=RACINE)
parser.add_argument("--performance", action="store_true", help="scénarios de performance (phase J)")
parser.add_argument("--seulement", default="", help="identifiants séparés par des virgules")
args = parser.parse_args()

code = os.path.abspath(args.code)
os.chdir(code)
sys.path.insert(0, code)                          # app/ et main.py de la version testée
sys.path.insert(1, os.path.join(RACINE, "tests"))  # scénarios de CE dépôt
os.environ.setdefault("DJANGO_API_URL", "http://django.test/api")
os.environ.setdefault("JWT_SECRET_KEY", "cle-de-test-suffisamment-longue-32o")
os.environ.setdefault("MODEL_QUEUE_TIMEOUT", "300")

import psutil  # noqa: E402

from app.core import config  # noqa: E402

config.settings.rate_limit_par_minute = 100_000
from app.nlu import model  # noqa: E402
from campagne.moteur import Banc, executer  # noqa: E402
from campagne.scenarios import TOUS  # noqa: E402

processus = psutil.Process()
chargement_s = None
if args.mode == "qwen":
    from app.assistant.synthese import CONSIGNES
    from app.nlu.extractor import SYSTEM_PROMPT
    debut = time.perf_counter()
    model.charger_modele([SYSTEM_PROMPT, CONSIGNES])
    chargement_s = round(time.perf_counter() - debut, 1)
    assert model.modele_disponible(), "modèle non chargé"
    print(f"Modèle chargé en {chargement_s} s, RSS {processus.memory_info().rss / 2**30:.2f} Gio", flush=True)

cas_retenus = [c for c in TOUS if not args.seulement or c.id in args.seulement.split(",")]
passes = []
for numero in range(args.passes):
    resultats = []
    for cas in cas_retenus:
        cpu = processus.cpu_times()
        r = executer(cas)
        cpu_s = sum(processus.cpu_times()[:2]) - sum(cpu[:2])
        resultats.append({"id": r.id, "categorie": r.categorie, "statut": r.statut, "raisons": r.raisons,
                          "reponses": r.reponses, "mesures": r.mesures, "cpu_s": round(cpu_s, 2)})
        marque = {"reussi": "OK ", "echoue": "KO ", "bloque": "BLQ"}[r.statut]
        duree = sum(m["ms"] for m in r.mesures)
        print(f"[passe {numero + 1}] {marque} {r.id:5} {duree:9.0f} ms  llm={sum(m['appels_llm'] for m in r.mesures)}"
              f"  {'; '.join(r.raisons)[:150]}", flush=True)
    passes.append(resultats)


def p95(valeurs):
    if not valeurs:
        return None
    return round(statistics.quantiles(valeurs, n=20, method="inclusive")[-1], 1) if len(valeurs) > 1 else valeurs[0]


# --- Phase J : performance -------------------------------------------------
performance = {}
if args.performance:
    from campagne.donnees import FauxDjangoCampagne, jeton

    def mesurer(nom, message, repetitions=5, preparer=None, utilisateur=1, sature=False):
        durees, llm = [], []
        for k in range(repetitions):
            if sature:
                # Verrou neuf à chaque répétition : le TestClient ouvre une boucle
                # d'événements par requête, et un verrou asyncio reste lié à la
                # première boucle qui l'a attendu (artefact du banc, pas de la production).
                model._verrou = asyncio.Lock()
                model._verrou._locked = True  # une autre génération occupe le modèle
            faux = FauxDjangoCampagne()
            if preparer:
                preparer(faux)
            from app.core import temps
            from campagne.donnees import DATE_REFERENCE
            horloge = temps.maintenant
            temps.maintenant = lambda: DATE_REFERENCE
            try:
                banc = Banc(faux)
                corps = banc.envoyer(message, utilisateur, session=f"perf-{nom}-{k}")
            finally:
                temps.maintenant = horloge
            durees.append(banc.mesures[-1]["ms"])
            llm.append(banc.mesures[-1]["appels_llm"])
        performance[nom] = {"message": message, "durees_ms": durees, "mediane_ms": statistics.median(durees),
                            "p95_ms": p95(durees), "froid_ms": durees[0],
                            "chaud_mediane_ms": statistics.median(durees[1:]) if len(durees) > 1 else None,
                            "appels_llm": llm, "type": corps.get("type"), "http": corps.get("_http"),
                            "reponse": (corps.get("reponse") or "")[:120]}
        print(f"[perf] {nom:28} médiane {performance[nom]['mediane_ms']:8.1f} ms  p95 {performance[nom]['p95_ms']:8.1f}"
              f"  froid {durees[0]:8.1f}  llm={llm}  [{corps.get('type')}]", flush=True)

    mesurer("J1_bonjour", "Bonjour.")
    mesurer("J2_comment_reserver", "Comment réserver un équipement ?")
    mesurer("J3_dispo_microscope", "Le microscope est-il disponible demain ?")
    mesurer("J4_preparer_reservation", "Prépare une réservation pour vendredi de 9 h à 11 h.")
    mesurer("J5_resume_hebdo", "Fais le résumé hebdomadaire de mes réservations.")
    mesurer("J7_django_indisponible", "Le microscope est-il disponible demain ?", preparer=lambda f: setattr(f, "panne", True))
    mesurer("J9_formulation_indirecte", "Je compte sur la centrifugeuse pour mon protocole", repetitions=3)

    # J6 : deux requêtes rapprochées de deux utilisateurs (dont une qui sollicite le modèle).
    async def deux_requetes():
        import httpx
        Banc(FauxDjangoCampagne())  # branche le faux Django
        from main import app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ia.test", timeout=600) as client:
            async def une(u, msg):
                t = time.perf_counter()
                r = await client.post("/api/chat", json={"session_id": f"perf-j6-{u}", "message": msg},
                                      headers={"Authorization": f"Bearer {jeton(u)}"})
                return round((time.perf_counter() - t) * 1000, 1), r.status_code
            return await asyncio.gather(une(1, "Je compte sur la centrifugeuse pour mon protocole"),
                                        une(2, "Quelles sont mes prochaines réservations ?"))
    (d1, h1), (d2, h2) = asyncio.run(deux_requetes())
    performance["J6_deux_requetes"] = {"requete_modele_ms": d1, "requete_regles_ms": d2, "http": [h1, h2]}
    print(f"[perf] J6 deux requêtes : avec modèle {d1} ms, règles seules {d2} ms (HTTP {h1}, {h2})", flush=True)

    model._verrou = asyncio.Lock()  # J6 a pu lier le verrou à sa boucle d'événements

    # J8 : modèle saturé (une génération occupe déjà le verrou) ou indisponible → repli sur les règles.
    if args.mode == "qwen":
        ancien = config.settings.model_queue_timeout
        config.settings.model_queue_timeout = 0.5
        try:
            mesurer("J8_modele_sature", "Je compte sur la centrifugeuse pour mon protocole", 3, sature=True)
        finally:
            model._verrou = asyncio.Lock()
            config.settings.model_queue_timeout = ancien
    else:
        mesurer("J8_modele_indisponible", "Je compte sur la centrifugeuse pour mon protocole", 3)

# --- Synthèse -------------------------------------------------------------
synthese = {}
for categorie in sorted({c.categorie for c in cas_retenus}):
    par_passe = [[r for r in p if r["categorie"] == categorie] for p in passes]
    durees = [m["ms"] for p in par_passe for r in p for m in r["mesures"]]
    derniere = par_passe[-1]
    synthese[categorie] = {
        "executes": len([r for r in derniere if r["statut"] != "bloque"]),
        "reussis": sum(r["statut"] == "reussi" for r in derniere),
        "echoues": sum(r["statut"] == "echoue" for r in derniere),
        "bloques": sum(r["statut"] == "bloque" for r in derniere),
        # Scénarios dont le statut change d'une passe à l'autre (réponses non déterministes).
        "instables": sorted(i for i in {r["id"] for r in derniere}
                            if len({r["statut"] for p in par_passe for r in p if r["id"] == i}) > 1),
        "mediane_ms": round(statistics.median(durees), 1) if durees else None,
        "p95_ms": p95(durees),
        "appels_llm": sum(m["appels_llm"] for p in par_passe for r in p for m in r["mesures"]),
        "messages": len(durees),
    }
    s = synthese[categorie]
    print(f"{categorie:14} {s['reussis']:3}/{s['executes']:<3} réussis  échoués {s['echoues']:2}  bloqués {s['bloques']}"
          f"  médiane {s['mediane_ms']} ms  p95 {s['p95_ms']} ms  appels LLM {s['appels_llm']}")

with open(os.path.abspath(os.path.join(RACINE, args.sortie)) if not os.path.isabs(args.sortie) else args.sortie, "w") as f:
    json.dump({"mode": args.mode, "code": code, "passes": args.passes, "chargement_modele_s": chargement_s,
               "rss_gio": round(processus.memory_info().rss / 2**30, 2), "synthese": synthese,
               "performance": performance, "resultats": passes}, f, indent=2, ensure_ascii=False)
