# Installer et mettre en route le compagnon

[English](installation.md) · **Français**

> **À lire avant de commencer.** Ce projet est une **exploration d'architecture**, publiée en
> cours de route. Il n'a **jamais tourné sur un vrai stream** : les connexions à Twitch, Kick et
> YouTube n'ont jamais parlé aux vrais serveurs, et il n'existe aucun retour d'expérience. Ce
> guide ne vous fait donc pas installer un produit fini : il vous fait installer un projet qui
> fonctionne, dont vous serez le premier utilisateur réel. Il n'y a pas de mise à jour
> automatique, pas d'installeur, pas de service qui redémarre tout seul, et personne qui
> corrigera un bogue à votre place. Si ce que vous cherchez, c'est un outil fiable aujourd'hui,
> ce n'est pas encore celui-ci — lisez la section [Ce qui n'est pas prouvé](#ce-qui-nest-pas-prouvé)
> à la fin.

Ce guide vous emmène de « je n'ai rien installé » à « mon assistant répond dans mon chat ».
Comptez **30 minutes**, dont la moitié passée à créer des comptes sur le web.

## 1. À qui ce guide s'adresse

Vous **n'avez pas besoin de savoir programmer**. Vous devez accepter trois choses :

- ouvrir une fenêtre noire appelée « terminal » et y **copier-coller** des commandes ;
- créer deux comptes sur le web (Twitch développeur, et un fournisseur de modèle de langage) ;
- recopier des valeurs d'un site vers un fichier, sans erreur de frappe.

Tout le reste est du copier-coller. Chaque commande est donnée **telle quelle**, avec, juste
après, ce que vous devez voir s'afficher. Si ce n'est pas ce que vous voyez, allez à la section
[12. Quand ça ne marche pas](#12-quand-ça-ne-marche-pas).

Un mot de vocabulaire, utilisé partout ensuite :

- **terminal** : la fenêtre où l'on tape les commandes (PowerShell ou Invite de commandes sous
  Windows, Terminal sous macOS, n'importe quel terminal sous Linux) ;
- **compagnon** : votre assistant, celui qui répond dans le chat ;
- **profil** : le fichier de configuration (`mon-compagnon.yaml` dans ce guide) ;
- **modèle** : l'IA qui rédige les réponses (ChatGPT, Claude, Mistral, un modèle local…).

## 2. La liste de courses

Préparez ces cinq choses **dans cet ordre**. La colonne « où » est un lien direct.

| # | Ce qu'il faut | Pourquoi | Où / Coût |
|---|---|---|---|
| 1 | **Python 3.10 ou plus récent** | le programme est écrit en Python | [python.org/downloads](https://www.python.org/downloads/) — gratuit. Sous Windows, **cochez « Add python.exe to PATH »** à l'installation, sinon rien ne marchera |
| 2 | **Un compte Twitch + une application développeur** | pour que le compagnon lise et écrive dans votre chat | [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps) → « Register Your Application ». Vous en sortez un **client id** et un **client secret**. Gratuit |
| 3 | **Un jeton d'accès Twitch + deux identifiants numériques** | le jeton est le mot de passe du bot ; les deux identifiants disent *quelle chaîne* et *quel compte bot* | [twitchtokengenerator.com](https://twitchtokengenerator.com) (site tiers, non affilié) ou l'outil officiel `twitch` CLI. Décochez tout sauf `chat:read` et `chat:edit`. Gratuit. **Le jeton expire : il faudra le refaire de temps en temps** |
| 4 | **Une clé de modèle de langage** | c'est le « cerveau » qui rédige les réponses | [OpenAI](https://platform.openai.com/) (payant à l'usage), [Groq](https://console.groq.com/), [DeepInfra](https://deepinfra.com/), [Mistral](https://console.mistral.ai/)… ou un modèle **local** avec [Ollama](https://ollama.com/) (gratuit, sur votre PC). **Il faut un modèle qui sait « appeler des outils » (function calling)** : sans ça, il démarrera mais ne pourra rien faire |
| 5 | **Le dossier du projet** | le logiciel lui-même | page GitHub du projet → bouton vert **Code** → **Download ZIP**, puis décompressez-le (ou `git clone` si vous connaissez) |

Ce que ça coûte : **0 €** pour Twitch et Python ; **de quelques centimes à quelques euros par
soirée** pour le modèle, facturé à l'usage. Un modèle local ne coûte rien mais consomme votre PC.

## 3. D'abord : voir à quoi ça ressemble (2 minutes, aucun compte)

Avant d'installer quoi que ce soit, sachez à quoi vous travaillez. Dans le dossier du projet,
dans un terminal :

```sh
python -m demo
```

Ce que vous devez voir : une petite histoire qui se déroule toute seule — un spectateur écrit, le
compagnon l'entend, réfléchit, capture l'audio, répond dans le chat et le dit à voix haute. C'est
une démonstration **hors ligne** : elle n'ouvre aucune connexion, n'utilise aucun compte et ne
coûte rien. Elle sert à vérifier que votre installation est vivante — le GIF du dépôt montre
exactement cette sortie.

## 4. Installer le projet

### 4.1 Ouvrir un terminal **dans le dossier du projet**

C'est le point qui rate le plus souvent. Le terminal doit être *à l'intérieur* du dossier
décompressé, sinon les commandes ne trouvent rien.

- **Windows** : ouvrez le dossier du projet dans l'Explorateur, cliquez sur la barre d'adresse,
  tapez `powershell`, appuyez sur Entrée.
- **macOS** : clic droit sur le dossier → Services → « Nouveau terminal au dossier ».
- **Linux** : clic droit dans le dossier → « Ouvrir dans un terminal ».

### 4.2 Installer

**Windows** (une ligne à la fois) :

```powershell
py -3 -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

**macOS et Linux** (une ligne à la fois) :

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Ce que vous devez voir : la dernière commande se termine par `Successfully installed …`, et votre
invite de commandes commence maintenant par `(.venv)`. C'est le signe que l'environnement est
actif.

> **À retenir :** `(.venv)` doit apparaître dans toutes les commandes suivantes. Le terminal
> oublie l'environnement quand vous le fermez : réactivez-le (la 2ᵉ ligne ci-dessus) à chaque
> nouvelle session.

## 5. Réunir les huit valeurs

Reprenez la liste de courses et remplissez ce tableau **dans un brouillon**. C'est la partie
fastidieuse, et une faute de frappe ici est la première cause d'échec.

| Valeur | Ce que c'est | Où la prendre |
|---|---|---|
| `TWITCH_CLIENT_ID` | identifiant public de votre application Twitch | site développeur Twitch, page de votre application |
| `TWITCH_CLIENT_SECRET` | mot de passe de votre application Twitch | même page, bouton « New Secret » |
| `TWITCH_ACCESS_TOKEN` | le mot de passe du bot | le générateur de jetons (droits `chat:read` + `chat:edit`) |
| `TWITCH_BROADCASTER_ID` | l'identifiant **numérique** de votre chaîne (pas votre pseudo) | le générateur de jetons l'affiche ; sinon un outil « Twitch user id lookup » |
| `TWITCH_BOT_USER_ID` | l'identifiant numérique du compte qui parle (le bot) | même méthode, avec le compte bot |
| `MODEL_ENDPOINT` | l'adresse du service de modèle | OpenAI : `https://api.openai.com/v1` · Ollama local : `http://localhost:11434/v1` · sinon l'adresse donnée par votre fournisseur |
| `MODEL_NAME` | le nom exact du modèle | chez votre fournisseur (ex. `gpt-4o-mini`, `llama-3.3-70b-versatile`) |
| `MODEL_API_KEY` | votre clé de modèle | la page « API keys » de votre fournisseur (Ollama local : n'importe quoi, `ollama` par exemple) |

> Le compte **bot** peut être votre propre compte, mais un second compte Twitch est plus propre :
> les messages du compagnon n'apparaîtront alors pas comme les vôtres.

## 6. Créer le fichier de configuration

À la racine du projet (le même dossier que `README.md`), créez un fichier nommé
**`mon-compagnon.yaml`** et collez-y exactement ceci :

```yaml
modules_directory: ./modules

enabled_modules:
  - twitch
  - chat_context
  - brain
  - audit

modules:
  twitch:
    client_id: ${TWITCH_CLIENT_ID}
    client_secret: ${TWITCH_CLIENT_SECRET}
    access_token: ${TWITCH_ACCESS_TOKEN}
    broadcaster_id: ${TWITCH_BROADCASTER_ID}
    bot_user_id: ${TWITCH_BOT_USER_ID}
    # Le nom que les spectateurs utilisent pour appeler le compagnon.
    # CHANGEZ-LE (et gardez-le court) : c'est le mot qui déclenche une réponse.
    companion_name: Assistant

  # Le compagnon garde en mémoire les derniers messages du chat.
  chat_context: {}

  brain:
    endpoint: ${MODEL_ENDPOINT}
    model: ${MODEL_NAME}
    api_key: ${MODEL_API_KEY}
    admission:
      session_queue_capacity: 4
      global_pending_capacity: 64
      max_sessions: 32
      workers: 4
      wait_seconds: 30
      total_run_seconds: 60
    budget:
      model_turns: 5
      model_call_seconds: 30
      action_seconds: 10
      max_tokens: 8192
      max_observation_bytes: 5242880
      max_action_calls: 6
      max_repeated_actions: 2
      delivery_reserve_seconds: 10
    conversation_memory:
      max_sessions: 64
      max_exchanges: 20
      max_bytes: 65536
      max_age_seconds: 3600
    fallback:
      enabled: true
      text: "Je n'ai pas pu répondre à temps."
    capabilities:
      required:
        - structured_output
    delivery:
      mode: fixed
      actions:
        - action: chat.write
          text_argument: text

  audit:
    output: stdout
    queue:
      max_records: 1000
      max_bytes: 4194304

secrets:
  - ${TWITCH_CLIENT_SECRET}
  - ${TWITCH_ACCESS_TOKEN}
  - ${MODEL_API_KEY}
  - ${MODEL_ENDPOINT}

actions:
  - rule_id: brain-reads-chat
    action_name: chat.read
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
      scope: chat
    principals: [brain]
    natures: [read]
    granted_permissions: [chat.read]
  - rule_id: brain-delivers-chat
    action_name: chat.write
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
      scope: chat
    principals: [brain]
    natures: [write]
    granted_permissions: [chat.write]

limits:
  bus_history:
    max_events: 1000
    max_bytes: 4194304
    max_age_seconds: 600
  observation_queue:
    max_records: 1000
    max_bytes: 4194304
  dedup:
    max_entries: 4096
    ttl_seconds: 600
  attachments:
    max_object_bytes: 1048576
    max_objects: 256
    max_total_bytes: 67108864
    max_bytes_per_run: 8388608
    ttl_seconds: 900
  conversation_memory:
    max_sessions: 64
    max_exchanges: 20
    max_bytes: 65536
    max_age_seconds: 3600
  chat_context:
    max_messages: 50
    max_bytes: 65536
    max_age_seconds: 900
    max_channels: 8
  admission:
    session_queue_capacity: 4
    global_pending_capacity: 64
    max_sessions: 32
    workers: 4
    wait_seconds: 30
    total_run_seconds: 60
```

Vous n'avez **rien à modifier** dans ce fichier, sauf `companion_name` (le nom du compagnon).
Les `${...}` sont des renvois aux variables du point suivant : le programme y mettra vos valeurs
tout seul, sans jamais les écrire dans le fichier.

En trois phrases, ce que dit ce profil :

- `enabled_modules` : les quatre briques activées — la connexion Twitch, la mémoire du chat, le
  cerveau, le journal ;
- `actions` : **ce que le compagnon a le droit de faire**, et rien d'autre. Ici, deux
  autorisations : lire le chat, y écrire. Hors de cette liste, tout est refusé — c'est le réglage
  le plus important du fichier, et le plus facile à étendre plus tard ;
- `limits` et les blocs `admission`/`budget` : les garde-fous (longueur de mémoire, nombre de
  réponses en attente, durée maximale d'une réponse). Les valeurs fournies sont saines : laissez-
  les tranquilles pour commencer.

## 7. Rendre les huit valeurs visibles par le programme

Le programme lit vos valeurs dans le **terminal**, pas dans un fichier. Tapez les huit lignes
ci-dessous dans le même terminal que celui où `(.venv)` apparaît — en remplaçant chaque valeur par
la vôtre, **sans guillemets** :

**Windows — PowerShell :**

```powershell
$env:TWITCH_CLIENT_ID="collez_ici"
$env:TWITCH_CLIENT_SECRET="collez_ici"
$env:TWITCH_ACCESS_TOKEN="collez_ici"
$env:TWITCH_BROADCASTER_ID="collez_ici"
$env:TWITCH_BOT_USER_ID="collez_ici"
$env:MODEL_ENDPOINT="collez_ici"
$env:MODEL_NAME="collez_ici"
$env:MODEL_API_KEY="collez_ici"
```

**Windows — Invite de commandes** (`cmd`) : même liste avec la forme `set TWITCH_CLIENT_ID=collez_ici`,
sans guillemets et sans espaces autour du `=`.

**macOS et Linux :**

```sh
export TWITCH_CLIENT_ID=collez_ici
export TWITCH_CLIENT_SECRET=collez_ici
export TWITCH_ACCESS_TOKEN=collez_ici
export TWITCH_BROADCASTER_ID=collez_ici
export TWITCH_BOT_USER_ID=collez_ici
export MODEL_ENDPOINT=collez_ici
export MODEL_NAME=collez_ici
export MODEL_API_KEY=collez_ici
```

Ces valeurs ne survivent pas à la fermeture du terminal. C'est normal, et c'est même souhaitable :
le jour où vous en aurez assez de les retaper, c'est le moment de lire
[`docs/config-ui.md`](config-ui.md) et de garder ce guide à portée de main.

## 8. Vérifier sans rien allumer

Avant de lancer quoi que ce soit en direct, demandez au programme de contrôler votre profil. Il
ne se connecte à rien, il ne lit aucun message :

```sh
python -m core.main --config mon-compagnon.yaml --check-config
```

Ce que vous devez voir : **rien du tout**, et un retour immédiat au prompt. C'est la bonne
nouvelle : `rien` = « tout est cohérent ». S'il y a un message d'erreur, il vous dit précisément
quel champ ne va pas — reportez-vous à la section [12](#12-quand-ça-ne-marche-pas).

## 9. Lancer le compagnon

```sh
python -m core.main --config mon-compagnon.yaml
```

Ce que vous devez voir : quatre lignes `ready` (les quatre briques qui démarrent), puis le
programme reste silencieux — il écoute. Il n'y a **rien à taper** : allez dans votre chat Twitch
et adressez-vous au compagnon par son nom :

```
Assistant, tu es là ?
```

Ce que vous devez voir ensuite : dans le chat, une réponse écrite par le compte bot ; dans le
terminal, une série de lignes (`chat`, `trigger`, `call`, `run`) qui racontent ce qu'il vient de
faire. Les lignes `audit` sont votre journal : elles disent exactement ce qui a été décidé.

Pour l'arrêter : `Ctrl` + `C` dans le terminal.

> Testez d'abord en **chat privé avec vous-même**, ou sur une chaîne de test, pas devant votre
> audience.

## 10. L'interface web de configuration

Retenir huit variables et éditer un YAML n'est pas tenable au quotidien. Le projet fournit une
petite interface web locale pour lire et modifier votre configuration :

```sh
python -m core.config_ui --config mon-compagnon.yaml
```

Ce que vous devez voir : une adresse à usage unique du type
`Configuration UI: http://127.0.0.1:8765/?token=…` — copiez-la dans votre navigateur. Elle ne
fonctionne que sur votre machine (elle n'est pas exposée sur internet) et le jeton change à
chaque lancement.

Trois choses à savoir sur cette interface :

- elle écrit vos modifications dans un **fichier séparé** (`config.local.yaml`) posé par-dessus
  votre profil : votre `mon-compagnon.yaml` reste intact, et supprimer une ligne de l'interface
  restaure la valeur d'origine ;
- les **secrets** et les **règles d'autorisation** (le bloc `actions`) y sont en lecture seule,
  volontairement : un clic de travers ne doit pas pouvoir élargir ce que le compagnon a le droit
  de faire ;
- appliquer un changement demande un **redémarrage à la main** : arrêtez (`Ctrl` + `C`) et
  relancez la commande du point 9.

> Le détail de cette interface est dans le guide opérateur [`docs/config-ui.md`](config-ui.md),
> également disponible [en français](config-ui.fr.md). Une honnêteté : la partie navigateur de cette
> interface n'a jamais été essayée dans un vrai navigateur (son contrat HTTP est testé, pas son
> rendu). Si un bouton vous semble inerte, c'est probablement là.

## 11. Ajouter la voix, OBS, les captures d'écran

Le profil du point 6 fait volontairement le minimum : il lit le chat et y répond. Tout le reste
est **modulaire** — chaque capacité est une brique que vous activez, avec ses propres réglages.
Vous n'avez rien à recomposer : les profils complets livrés avec le projet sont vos modèles.

| Ce que vous voulez | Brique à activer | Ce qu'il faut en plus, en dehors du projet |
|---|---|---|
| entendre le compagnon dans le stream | `audio_output` | un service de synthèse vocale (endpoint + clé), un lecteur audio installé |
| que le compagnon vous entende | `audio_input` | un micro, un enregistreur en ligne de commande, un service de transcription |
| changer vos scènes, vos alertes | `stream_control` | OBS avec `obs-websocket` activé |
| parler d'une capture d'écran | `capture` | un outil de capture d'écran en ligne de commande |
| reconnaître les habitués | `users`, `viewer_memory` | rien |
| modérer, faire un clip, un sondage | `moderation`, `clips`, `twitch` | des droits supplémentaires sur votre jeton Twitch |
| séparer le cerveau du PC de streaming | `proxy`, `agent_link` | un second ordinateur ou un serveur ([protocole](proxy-protocol.md)) |

Partez de `config.yaml.example` (le commentaire au-dessus de chaque ligne explique le réglage) et
de `presence.yaml.example` (le profil complet : personnalité, mémoire, enchaînements). Copiez les
blocs qui vous intéressent dans votre fichier. À chaque ajout, refaites la vérification du point 8
avant de relancer : le message d'erreur vous dira quel réglage manque.

## 12. Quand ça ne marche pas

Les messages ci-dessous sont ceux que le programme affiche réellement. Le premier `Aucun compte`
veut dire : lisez d'abord la ligne juste en dessous.

| Ce que vous voyez | Ce que ça veut dire | Quoi faire |
|---|---|---|
| `environment reference is unresolved` | une `${...}` de votre profil ne correspond à aucune variable de votre terminal | vérifiez au point 7 que les huit valeurs sont définies **dans ce terminal-ci** (le terminal oublie tout quand vous le fermez) |
| `twitch authentication: rejected (status 401)` | Twitch a refusé votre jeton | votre `TWITCH_ACCESS_TOKEN` est faux, mal copié ou expiré : refaites-en un, et vérifiez que `TWITCH_CLIENT_ID` correspond bien à la même application |
| `error: module 'brain': phase 'prepare' failed` | le cerveau n'a pas pu joindre le modèle ou n'a pas reçu de réponse utilisable | vérifiez `MODEL_ENDPOINT` (une barre oblique finale en trop suffit à casser l'adresse), `MODEL_NAME` (orthographe exacte) et `MODEL_API_KEY`, puis que le modèle **sait appeler des outils**. Soyez prévenu : aujourd'hui ce message **ne dit pas** laquelle de ces causes c'est — c'est une limite connue du projet |
| `secrets[N]: is referenced by no setting` | vous avez listé dans `secrets:` une variable qui n'est utilisée nulle part | supprimez la ligne en question du bloc `secrets` |
| `--check-config` sur un fichier `.example` livré : `environment reference is unresolved` | normal : ces exemples citent des variables d'exemple | contrôlez **votre** fichier, pas les `.example` |
| Le compagnon démarre mais ne répond jamais | il n'est pas appelé, ou il n'a pas le droit de répondre | écrivez son nom exactement comme dans `companion_name` ; vérifiez que votre jeton porte les droits `chat:read` **et** `chat:edit` ; vérifiez que le compte bot a bien accès à votre chaîne |
| Il répond « en retard » ou pas du tout au bout d'un moment | le jeton Twitch a expiré (environ 60 jours) | refaites le jeton, relancez |
| La réponse est vide ou coupée | soit le modèle « réfléchit » et consomme tout son budget, soit la limite de longueur est trop basse | dans le profil, montez `brain.budget.max_tokens` |
| Erreur de facturation chez votre fournisseur | votre clé de modèle est facturée à l'usage | rien à voir avec le projet : surveillez votre compte, ou passez à un modèle local |

## 13. Ce qui n'est pas prouvé

À garder en tête avant de vous lancer en direct :

- **aucun stream réel n'a jamais tourné avec ce logiciel** ;
- **les connexions à Twitch, Kick et YouTube n'ont jamais parlé aux vrais serveurs** : elles sont
  testées contre des serveurs simulés. L'authentification, les limites de débit et les
  particularités de ces services ne sont pas vérifiées ;
- **l'interface web n'a jamais été essayée dans un vrai navigateur** ;
- **pas de durcissement de production** : pas d'installation système, pas de mise à jour, pas de
  supervision, pas d'essai de charge ;
- le projet est publié sous **WTFPL** : utilisez-le, modifiez-le, redistribuez-le librement, et
  **sans aucune garantie ni aucun support**.

Concrètement, pour une soirée en direct : commencez en chat privé ou sur une chaîne de test,
gardez la main sur la modération, et soyez prêt à couper (`Ctrl` + `C`) si le compagnon part dans
une direction que vous n'aimez pas.

## 14. Pour aller plus loin

| Document | Contenu |
|---|---|
| `README.md` | la vue d'ensemble, ce qui est prouvé et ce qui ne l'est pas, et la démo hors ligne |
| [`docs/config-ui.md`](config-ui.md) | le guide de l'interface web (également [en français](config-ui.fr.md)) |
| [`docs/proxy-protocol.md`](proxy-protocol.md) | faire tourner le cerveau sur un autre ordinateur (normatif ; également [en français](proxy-protocol.fr.md), traduction de courtoisie) |
| [`docs/design-v2.md`](design-v2.md) | la conception d'ensemble : pourquoi les briques sont modulaires (également [en français](design-v2.fr.md)) |
| `config.yaml.example`, `presence.yaml.example` | les profils complets, commentés ligne à ligne |
| `python -m demo --platform fake` | la même démonstration sur une seconde plateforme, pour voir que le cœur ne dépend d'aucune en particulier |
