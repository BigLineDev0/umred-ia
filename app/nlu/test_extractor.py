import asyncio

from extractor import extraire_intention


async def main():

    messages = [
        "Je veux réserver le spectrophotomètre demain de 9h à 11h.",
        "Est-ce que le microscope est disponible demain ?",
        "Quelles sont mes réservations ?",
        "Je veux annuler ma réservation.",
    ]

    for message in messages:

        print("\nUtilisateur :", message)

        resultat = await extraire_intention(message)

        print("IA :", resultat)


if __name__ == "__main__":
    asyncio.run(main())