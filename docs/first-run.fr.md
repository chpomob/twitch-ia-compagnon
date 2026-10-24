# Le premier lancement — du lanceur à une première réponse

[English](first-run.md) · **Français**

Ce guide s'adresse à un streamer sur son propre PC, une fois le compagnon installé. Il couvre les
quinze premières minutes : démarrer le compagnon depuis son lanceur, passer les cinq écrans de
configuration, et le regarder travailler dans la console. Rien n'y demande de terminal ni de fichier
YAML. Le texte anglais fait foi ; cette traduction le suit.

Là où ce guide et la spécification de la phase 9 (`docs/campaigns/phase9/spec.md`) diffèrent, la
spécification l'emporte.

## 1. Avant de commencer : l'installation

L'installation vient **avant** les quinze minutes, et quelqu'un d'autre peut la faire pour vous (un
ami, l'opérateur). C'est la seule partie qui utilise encore un terminal :

1. Installez Python et le projet exactement comme le décrit [`docs/installation.md`](installation.md)
   dans ses sections 2 et 4 (la liste de courses et *Installer le projet*). Les sections 5 à 9 de ce
   guide — rassembler les valeurs, écrire un fichier YAML, définir des variables d'environnement — ne
   sont **pas** nécessaires au premier lancement : les écrans de configuration ci-dessous les
   remplacent.
2. Dans le même terminal, l'environnement toujours actif, écrivez le lanceur :

```sh
python -m core.config_ui --install-launcher
```

La commande écrit un lanceur pour votre compte, affiche son chemin (`Launcher: …`) puis se termine.
Elle ne démarre rien. Le lanceur exécute le Python même dans lequel le projet a été installé, avec
`python -m core.config_ui` et aucun autre argument : il ouvre donc toujours le profil par défaut
ci-dessous.

| Plateforme | Lanceur écrit | Où le trouver |
|---|---|---|
| Linux | `twitch-ia-compagnon.desktop` dans `~/.local/share/applications/` | le menu des applications |
| Windows | `twitch-ia-compagnon.cmd` dans `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` | le menu Démarrer |
| macOS | `twitch-ia-compagnon.command` dans `~/Applications/` | le dossier Applications de votre dossier personnel |

Relancer `--install-launcher` remplace un lanceur qu'il a écrit ; un fichier à cet endroit qu'il n'a
pas écrit est laissé tel quel, et la commande le dit. Le lanceur ne contient jamais de jeton ni de mot
de passe.

À partir d'ici, plus aucun terminal.

## 2. Démarrer depuis le lanceur

Cliquez sur le lanceur comme sur n'importe quelle application. Le compagnon ouvre de lui-même votre
navigateur par défaut sur sa page ; l'adresse porte un jeton d'accès à usage unique et ne fonctionne
que sur cet ordinateur (`127.0.0.1`). La première fois, la page est la configuration (l'*assistant*) ;
une fois la configuration terminée, le même clic ouvre la console.

Cliquer de nouveau sur le lanceur alors que le compagnon est déjà ouvert n'en démarre pas un second :
cela ouvre la page de celui qui tourne déjà. Si c'est une autre version qui tourne, rien n'est ouvert
et la page de celle qui tourne est indiquée à la place. Si le lancement précédent ne s'est pas arrêté
proprement, la page dit ce qu'elle a trouvé et quoi faire ; elle ne supprime jamais en silence
l'enregistrement d'un compagnon qui est peut-être encore en train de démarrer.

Sous Windows, un second clic ne peut pas rouvrir la page : l'enregistrement du compagnon en cours n'y
garde pas son jeton d'accès, donc le nouveau lancement ne peut pas confirmer celui qui tourne et
n'ouvre rien. Il indique quel processus détient l'enregistrement et sur quel port le compagnon écoute.
Revenez à l'onglet du navigateur que le premier lancement a ouvert (la page est sur `127.0.0.1` à ce
port) ; si vous l'avez fermé, fermez le programme qui détient ce processus, ou redémarrez l'ordinateur,
et cliquez de nouveau sur le lanceur, qui ouvre une nouvelle page.

## 3. Où vivent vos fichiers

Démarré depuis le lanceur, le compagnon utilise un seul profil, dans le dossier propre à votre compte.
« Premier lancement » veut dire : ce fichier n'existe pas encore.

| Plateforme | Profil par défaut |
|---|---|
| Linux | `$XDG_CONFIG_HOME/twitch-ia-compagnon/profile.yaml` (`~/.config/twitch-ia-compagnon/profile.yaml` quand `XDG_CONFIG_HOME` n'est pas définie) |
| Windows | `%APPDATA%\twitch-ia-compagnon\profile.yaml` |
| macOS | `~/Library/Application Support/twitch-ia-compagnon/profile.yaml` |

À côté du profil, le compagnon garde ses propres petits fichiers : l'avancement de la configuration
(`profile.yaml.wizard.json`), l'enregistrement de la fenêtre en cours (`profile.yaml.instance.json`),
l'état du programme (`profile.yaml.status.json`), le fil de la console (`profile.yaml.feed.jsonl`) et,
dès que vous utilisez l'éditeur, vos modifications (`profile.local.yaml`). Aucun ne contient vos clés
ni vos jetons, à une exception près : sous Linux et macOS, l'enregistrement de la fenêtre en cours
contient l'adresse de la page avec son jeton d'accès, pour qu'un second clic puisse rouvrir la page.
Ce fichier n'est lisible que par vous et il est supprimé quand le compagnon s'arrête proprement ; tant qu'il
existe, traitez-le comme un mot de passe et ne le partagez pas.

### Le fichier d'identifiants

Les clés et jetons que vous saisissez pendant la configuration ne sont **pas** dans le profil. Le
profil ne fait que les nommer (`${TWITCH_ACCESS_TOKEN}` et autres). Leurs valeurs sont gardées dans un
fichier d'identifiants, nommé d'après le nom de fichier du profil suivi de `.credentials.json` —
`<profile-file-name>.credentials.json`, donc `profile.yaml.credentials.json` pour le profil par
défaut. Deux profils n'en partagent jamais un : `a.yaml` et `a.yml` ont deux fichiers.

Le fichier d'identifiants vit dans un dossier propre à l'utilisateur, que seul votre compte peut
ouvrir :

| Plateforme | Dossier des identifiants | Protection |
|---|---|---|
| Linux | `$XDG_CONFIG_HOME/twitch-ia-compagnon/` (`~/.config/twitch-ia-compagnon/`) | le dossier n'est qu'à vous (`0700`), le fichier n'est lisible que par vous (`0600`) |
| Windows | `%LOCALAPPDATA%\twitch-ia-compagnon\` | les données d'application locales de votre compte, que Windows réserve à votre compte |
| macOS | `~/Library/Application Support/twitch-ia-compagnon/` | le dossier n'est qu'à vous (`0700`), le fichier n'est lisible que par vous (`0600`) |

L'écran 4 indique le dossier utilisé et pourquoi. Là où l'ordinateur n'offre pas cette protection —
sous Linux ou macOS un dossier qui appartient à un autre compte ou dans lequel d'autres comptes
peuvent écrire, sous Windows l'absence de dossier `%LOCALAPPDATA%` — le compagnon ne stocke vos
identifiants **nulle part**, le dit, et l'écran 4 reste fermé tant que le dossier n'est pas corrigé.

Les valeurs ne sont plus jamais affichées : ni sur une page, ni dans un journal, ni dans la console,
ni dans la vue du profil en lecture seule. Un champ déjà rempli est affiché vide et marqué *conservé*.

**Seule la fenêtre du compagnon lit ce fichier.** Le bouton **Démarrer** de la console donne les
valeurs stockées au programme qu'il lance. Un programme démarré depuis un terminal
(`python -m core.main --config …`) ne lit **pas** le fichier d'identifiants : il ne lit que les
variables d'environnement, comme le décrit la section 7 de [`docs/installation.md`](installation.md).

## 4. Les cinq écrans de configuration

Chaque écran ne s'ouvre qu'une fois le précédent **réellement** réussi : la vérification est faite par
le compagnon, pas par une case que vous cochez. Si quelque chose échoue, l'écran reste ouvert et
affiche une phrase qui dit ce qui n'a pas marché et quoi faire ensuite ; une commande *Afficher le
détail brut*, fermée par défaut, contient le détail technique quand il y en a un. Modifier quelque
chose sur un écran déjà passé rouvre cet écran et tous les suivants. Fermer la fenêtre en cours de
route ne risque rien : le clic suivant sur le lanceur reprend la configuration.

1. **Langue et profil.** Choisissez l'anglais ou le français (modifiable sur chaque page), puis l'un
   des quatre profils prêts à l'emploi, et remplissez ce qu'il demande :
   - **Questions du chat** — répond à un message du chat qui mentionne le nom du compagnon (demande
     ce nom) ;
   - **Points de chaîne** — réagit quand un spectateur échange une récompense personnalisée que vous
     choisissez (la récompense est choisie à l'écran 2) ;
   - **Mot-clé parlé** — écoute votre micro et répond quand vous dites le mot d'éveil (demande le mot
     d'éveil, la commande d'enregistrement, le détecteur et les réglages de transcription) ;
   - **Silencieux** — se connecte au chat de votre chaîne mais ne répond jamais : tous les
     déclencheurs sont éteints.
2. **Votre compte Twitch.** Saisissez le client id et le client secret de votre application Twitch et
   le jeton d'accès du compte au nom duquel parle le compagnon. C'est Twitch qui les vérifie : le jeton
   doit être valide, appartenir à cette application et porter les permissions dont le profil a besoin.
   L'écran affiche ensuite la chaîne que nomme le jeton ; vous ne tapez jamais d'identifiant numérique.
   Pour *Points de chaîne*, les récompenses personnalisées de la chaîne sont listées et vous en
   choisissez une.
3. **Le modèle.** Saisissez l'adresse du modèle, son nom et sa clé d'API. Le compagnon envoie une vraie
   requête et ne valide l'écran que si le modèle répond par du texte. Pour *Silencieux*, cet écran est
   marqué *non nécessaire*.
4. **Votre profil.** *Écrire le profil et le vérifier* écrit le profil à l'emplacement par défaut,
   stocke vos identifiants dans le fichier d'identifiants et lance la même vérification de
   configuration que `--check-config`. *Afficher le profil (lecture seule)* montre ce qui a été écrit —
   vous n'avez jamais à le modifier. La configuration n'écrit jamais que des fichiers qu'elle a écrits
   elle-même : un profil qui existait déjà, ou que quelqu'un d'autre a modifié depuis, est laissé tel
   quel et l'écran vous renvoie vers l'éditeur.
5. **Un essai à blanc.** *Lancer l'essai à blanc* fait passer un faux événement du type de votre profil
   à travers celui-ci. Rien n'est publié sur votre chaîne. L'écran n'est validé que si l'essai
   enregistre un run, et il l'affiche : l'identifiant du run et son résultat. Pour *Silencieux*, le
   résultat est « rien n'a été déclenché », comme prévu. La configuration est alors terminée ; *Ouvrir
   la console* vous y emmène.

La configuration ne démarre jamais le compagnon sur votre chaîne : c'est le rôle du bouton
**Démarrer** de la console.

## 5. La console : est-ce que ça marche ?

La console répond à une question, *est-ce que ça marche ?*, par des phrases simples dans votre langue :

- **État** — en marche depuis cette fenêtre, arrêté, ou un autre processus du compagnon que cette
  fenêtre n'a pas démarré et ne contrôle pas ;
- **Modules** — chaque module activé, prêt ou dégradé ; pour un module dégradé, sa cause et quoi
  faire ;
- **Ce qui vient de se passer** — le fil, du plus récent au plus ancien, rafraîchi tout seul : le
  message d'un spectateur qui arrive, un déclencheur qui l'accepte ou le laisse passer, une réponse
  mise en file, commencée et terminée, une action refusée, chacun avec sa raison en une phrase ;
- **Démarrer** et **Arrêter** — **Démarrer** lance le compagnon avec votre profil et vos identifiants
  stockés, seulement si aucun autre compagnon ne tient ce profil ; **Arrêter** n'arrête que celui que
  cette fenêtre a démarré. Les deux disent ce qui s'est passé. Si le compagnon s'arrête pendant son
  démarrage, la console affiche la cause et l'action suivante, et les dernières lignes du programme
  derrière *Afficher le détail brut*, fermé par défaut et purgé de tout secret ;
- **Ce que le compagnon a le droit de faire** — les autorisations configurées (règle, action, qui,
  où), chacune avec un bouton **Révoquer**. Révoquer retire l'autorisation et, si le compagnon tourne
  depuis cette fenêtre, le redémarre sans elle. La console ne peut que retirer des permissions : rien
  n'y permet d'en ajouter ou d'en élargir une.

## 6. Passer à l'éditeur

Quand le profil prêt à l'emploi ne suffit plus, *Ouvrir l'éditeur* (dans la console) ouvre l'éditeur
de configuration existant sur le même profil. C'est l'outil de l'opérateur, décrit dans
[`docs/config-ui.md`](config-ui.md) : les réglages de chaque module, *Check*, *Apply*. Il ne réécrit
jamais votre profil ; vos modifications vont dans `profile.local.yaml` à côté de lui, posées par-dessus
le profil, et retirer une modification y rétablit la valeur du profil. Les secrets et les permissions y
restent en lecture seule ; le fichier d'identifiants reste celui qu'utilise la console.

Une fois la configuration terminée, elle n'est plus proposée pour ce profil : les modifications
suivantes relèvent de l'éditeur. Un opérateur qui préfère les fichiers peut aussi démarrer la même
page sur n'importe quel profil avec `--config`, comme le décrit [`docs/config-ui.md`](config-ui.md) ;
elle s'ouvre alors sur l'éditeur.

## 7. Ce qui n'est pas prouvé

L'affirmation des quinze minutes — d'une installation fonctionnelle à un premier run en moins de
quinze minutes, sans terminal et sans YAML — ne repose que sur l'essai enregistré
(`docs/campaigns/phase9/trial-record.md`), et ne vaut que pour la plateforme que cet enregistrement
nomme. L'installation elle-même demande encore un terminal ; cette étape est hors des quinze minutes
et figure parmi les limites de l'enregistrement.
