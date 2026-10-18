# Enregistrer les résultats CSV dans ton Google Drive

L’application peut utiliser Google Drive à la place de Supabase. Chaque lot
terminé produit **un fichier CSV de 10 lignes de votes**, en plus de l’en-tête.
Les fichiers restent dans un dossier privé de ton Drive. Les participants
n’ont pas besoin de compte Google et ne voient ni le dossier ni les fichiers.

Le petit récepteur Google Apps Script s’exécute avec ton compte et crée les
fichiers dans ce dossier. Le serveur Streamlit signe chaque envoi avec un secret
partagé. Le secret reste dans les réglages de Streamlit et du script.

## 1. Préparer le dossier et le script Google

1. Crée un dossier privé dans ton Drive, par exemple `Pixel Art Study — Results`.
   Dans son adresse `https://drive.google.com/drive/folders/IDENTIFIANT`, copie
   uniquement la partie `IDENTIFIANT`.
2. Ouvre [Google Apps Script](https://script.google.com/) avec ton compte Google
   et crée un projet. Remplace le contenu de `Code.gs` par le fichier
   [Code.gs](Code.gs) fourni dans ce dépôt.
3. Dans **Paramètres du projet → Propriétés du script**, ajoute :

   | Propriété | Valeur |
   | --- | --- |
   | `RESULTS_FOLDER_ID` | L’identifiant du dossier créé à l’étape 1 |
   | `SHARED_SECRET` | Un secret aléatoire d’au moins 32 caractères |

   Pour générer le secret sur ton ordinateur :

   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

   Conserve ce secret dans tes réglages privés. Ne le mets pas dans le code,
   dans GitHub, dans l’URL ou dans les messages des participants.
4. Dans l’éditeur, sélectionne la fonction **checkSetup** et exécute-la. Autorise
   le projet à accéder à ton Drive avec ton compte. Cette vérification n’écrit
   aucun résultat. Le projet doit appartenir à une personne autorisée à créer
   des fichiers dans le dossier.
5. Clique sur **Déployer → Nouveau déploiement → Application Web** :
   - **Exécuter en tant que : Moi**.
   - **Qui a accès : Tout le monde** (sans connexion Google).
6. Copie l’URL du déploiement qui se termine par **/exec**, et non `/dev`.

L’adresse du récepteur est accessible sans connexion pour que le serveur
Streamlit puisse l’appeler. Toute écriture exige néanmoins une signature HMAC
valide. Cette option ne rend pas ton dossier ni tes CSV publics. N’ajoute pas
de partage public au dossier. Si ton établissement interdit les applications
Web accessibles sans connexion, il faudra faire autoriser ce déploiement par
l’administrateur ou conserver l’autre stockage disponible.

Ces réglages suivent le [guide officiel des applications Web Apps Script](https://developers.google.com/apps-script/guides/web).

## 2. Configurer Streamlit

Sur Streamlit Community Cloud, ouvre les réglages de l’application puis la
section **Secrets**. En local, crée `.streamlit/secrets.toml`. Renseigne :

```toml
[storage]
backend = "google_drive"

[google_drive]
web_app_url = "https://script.google.com/macros/s/TON_DEPLOIEMENT/exec"
shared_secret = "LE_MEME_SECRET_ALEATOIRE_QUE_DANS_APPS_SCRIPT"
```

Le secret doit être exactement le même que `SHARED_SECRET`. Supabase n’est pas
nécessaire pour ce mode. Aucun compte de service ni clé Google Cloud n’est requis.

Désactive le mode aperçu, sinon l’application n’écrira aucun résultat :

```powershell
# PowerShell : retirer la variable avant de relancer Streamlit
Remove-Item Env:PIXEL_STUDY_PREVIEW -ErrorAction SilentlyContinue
streamlit run app.py
```

Redémarre l’app après avoir configuré les secrets et ouvre une nouvelle session.
Un lot en cours conserve son stockage d’origine jusqu’à la fin de sa session.

## 3. Vérifier un premier lot

Utilise d’abord un dossier de test. Termine 10 comparaisons et vérifie :

- Un CSV apparaît dans le dossier.
- Il contient 10 votes, avec le même `participant_id` et le même `batch_id`.
- `batch_comparison_number` va de 1 à 10 et chaque vote possède un gagnant.
- Un second lot conserve `participant_id`, utilise un nouveau `batch_id`, et
  `comparison_number` passe de 11 à 20.

Les fichiers portent des noms comme :

```text
pixel_votes_<participant_id>_batch_000001.csv
pixel_votes_<participant_id>_batch_000002.csv
```

Chaque fichier conserve les méthodes, positions, prompts disponibles,
résolutions, hashes d’images, choix, horodatages UTC, numéro du lot et paramètres
de tirage. `submission_sha256` identifie le contenu exact de l’envoi.

Une nouvelle tentative après une coupure réutilise le même fichier. Le script
vérifie son contenu complet avant d’accuser réception ; il refuse une tentative
différente pour le même participant et numéro de lot. Un verrou sérialise les
écritures, et le fichier est créé en entier, sans ajouter des lignes dans un CSV
commun. Aucune réponse d’un autre participant n’est écrasée.

Ne déplace pas et ne modifie pas les CSV du dossier de collecte pendant les
sessions : ils servent aussi à vérifier les nouvelles tentatives et la séquence
des lots. Télécharge ou copie les fichiers pour l’analyse.

## Récupérer un CSV global pour l’analyse

Télécharge les CSV depuis Drive et décompresse-les dans un dossier local. Puis :

```bash
python tools/merge_drive_csv.py --input ./csv-telecharges --output results.csv
```

Le programme vérifie que les lots sont complets, refuse les conflits, élimine les
copies strictement identiques d’un même lot et trie les votes par participant et
numéro de comparaison. Il conserve les valeurs et les textes d’origine.

## Limites pratiques

La collecte dépend de ton espace Drive et des
[quotas Apps Script](https://developers.google.com/apps-script/guides/services/quotas).
Vérifie ces limites pour la taille de ton étude. Un quota dépassé ou un service
indisponible déclenche le bouton Réessayer ; le lot reste dans la session active
et les lots déjà créés restent dans Drive.

Après une modification de `Code.gs`, mets le déploiement à jour via
**Déployer → Gérer les déploiements → Modifier → Nouvelle version**. Enregistrer
le code seul ne met pas à jour le récepteur `/exec`.
