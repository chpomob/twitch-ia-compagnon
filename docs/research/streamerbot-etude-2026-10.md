# Streamer.bot — ce qu'il permet, comment on l'interroge, et comment le compagnon s'y branche

Étude d'intégration menée le 15 octobre 2026 pour `twitch-ia-compagnon`, à la demande d'un cas réel :
un ami streamer fait déjà tourner Streamer.bot et veut l'utiliser **via** le compagnon.

Objectif : savoir ce que Streamer.bot expose réellement, ce qu'il ne permet pas, et par quelles
architectures le compagnon peut s'y brancher sans casser ses propres invariants (default-deny, budgets
bornés, résultats terminaux explicites).

Toutes les affirmations ci-dessous viennent des sources listées en fin de document, registre
[`sources-ledger-streamerbot.json`](sources-ledger-streamerbot.json). **Aucun essai réel contre une
instance Streamer.bot en fonctionnement n'a été fait pour cette étude** : la machine de référence est
Linux, Streamer.bot n'y est pas installé, et les points marqués « à vérifier » le sont. C'est
précisément ce que la section 9 propose de mesurer.

## 1. Résumé exécutif

- Streamer.bot est un **automate d'événements de stream gratuit**, binaire fermé, développé par
  nate1280 depuis 2021[19], **Windows d'abord** : application portable, sans installation, avec
  auto-updater et canaux stable / beta / alpha[21]. Linux et macOS existent en **expérimental** via
  Wine, sans support officiel[22][24][23].
- La version stable courante est la ligne **1.0.5 (binaire 1.0.7)**[18], qui a fait passer les serveurs
  HTTP et WebSocket sur Kestrel et **migré complètement le chat Twitch vers EventSub** ; la 1.0.0
  apportait Kick, la refonte UI, Decks 2.0 et .NET 4.8.1[18][19].
- Il expose **quatre surfaces d'interaction**, toutes locales par défaut : un **serveur WebSocket**
  (`127.0.0.1:8080`, 22 requêtes, authentification optionnelle)[2][3][4], un **serveur HTTP**
  (`127.0.0.1:7474`, `POST /DoAction`, réponse `204`)[8][9], un **serveur UDP** (`DoAction` sans
  réponse)[10], et un **client WebSocket sortant** plus un **serveur WebSocket « custom »** qui font
  entrer du texte de l'extérieur dans les triggers de l'application[29][30].
- Le catalogue est énorme et c'est là sa valeur : **444 types de sub-actions** (scènes et sources OBS,
  clips, modération Twitch/Kick/YouTube, rewards, sondes et prédictions, raid, Stream Deck, sons,
  fichiers, TTS Speaker.bot, signaux…)[11] et **444 types de triggers**[12], plus ~175 méthodes C#
  « cœur » et ~215 méthodes d'intégration exécutables dans l'application[13][14].
- Le point dur, et il est décisif pour nous : **`DoAction` est fire-and-forget**. Le `status: ok` de la
  réponse signifie « requête acceptée », pas « action réussie »[2] ; le HTTP répond `204 No Content`
  sans corps[9] ; le projet tiers qui fait déjà ce branchement pour un assistant IA le documente
  explicitement comme un avertissement[37]. Une réponse structurée n'est possible que si **l'action
  Streamer.bot elle-même** se termine par le sub-action *Trigger Custom Event*, avec une corrélation
  côté client (argument `sbClientResponse`, timeout 10 s par défaut)[15][17][28].
- Conséquences pour le compagnon : Streamer.bot est un excellent **adaptateur de plateformes déjà
  installé** (il sait lire le chat, la présence, les crédits, et il sait agir sur OBS, sur les
  plateformes et sur des dizaines de services) mais c'est une frontière **sans confirmation**, à
  traiter comme telle côté exécuteur d'actions : `nature: write`, `idempotency: none`, et un résultat
  `external_unknown` par défaut.

## 2. Méthode

1. Documentation officielle, lue en direct : référence WebSocket (serveur, requêtes, guides,
   authentification), serveur HTTP, serveur UDP, référence des sub-actions et des triggers, méthodes C#,
   FAQ plateformes[1][2][3][4][5][6][8][9][10][11][12][13][14][25][26][33].
2. **Markdown source** de cette documentation (dépôt `Streamerbot/docs`), plus fiable que le site
   statique pour les pages dont le contenu est rendu en JavaScript : installation Windows/Linux/macOS,
   changelogs 1.0.0 et 1.0.1, guide Actions, FAQ file bloquante, exemple POST en C#, sub-action Fetch URL
   et Trigger Custom Event, triggers WebSocket Client et Custom Server, variables du client[19][20][21][22][24][26][27]
   [28][29][30][31][32][41].
3. API publiques : releases (canal stable et numéro de version)[18], dépôts de l'organisation
   `Streamerbot` (client officiel, docs, script Linux, wiki)[35], dépôt de l'application (absence de
   licence et de release → binaire fermé distribué hors GitHub)[36], métadonnées du client Python
   communautaire[34].
4. Client officiel TypeScript : documentation des actions et **lecture du code** de la méthode
   `doActionWithCustomEventResponse`, parce que le mécanisme de réponse n'est pas décrit côté serveur :
   il est implémenté par le client[15][17].
5. Un précédent d'intégration IA vers Streamer.bot, pour voir comment un tiers traite la frontière
   (et quels défauts il annonce franchement)[37].
6. Chiffres d'adoption : uniquement sur extraits de recherche, donc signalés comme tels[38][39].

Ce qui n'a **pas** été fait : aucune instance Streamer.bot interrogée, aucun `DoAction` envoyé, aucune
version installée, aucun test Wine. Les sections 4, 6 et 9 distinguent partout le documenté du mesuré.

## 3. Ce qu'est Streamer.bot, en une page

| Dimension | État documenté |
| --- | --- |
| Nature | Automate d'événements et d'actions pour streamers, application de bureau portable (pas d'installateur), écrite en .NET 4.8.1[19][21] |
| Éditeur / licence | nate1280 ; binaire **fermé** : le dépôt GitHub de l'application ne porte ni licence ni release[36] |
| Première version publique | 20 août 2021 (précédé d'un an d'usage interne sous le nom ChannelPointsHandler)[19] |
| Version stable au 15/10/2026 | canal stable **1.0.7** (ligne 1.0.5 + correctifs)[18] |
| Plateformes de diffusion | Twitch, YouTube (PushAPI pour le chat), **Kick seulement depuis 1.0.0** et via webhooks, donc avec des délais possibles ; TikTok non (intégration tierce TikFinity)[20][25] |
| Écosystème d'intégrations | OBS Studio, Streamlabs Desktop, Meld Studio, Polypop, Elgato (Stream Deck, Wave Link, Control Center, Camera Hub), VTube Studio, Voicemod, Lumia Stream, Crowd Control, Speaker.bot, Discord, IFTTT, monétisation (Ko-fi, Patreon, StreamElements, Streamlabs, Tipeee, Throne, Fourthwall, TreatStream, Shopify…), jeux de chat (T.I.T.S.)[11][12] |
| Système hôte | **Windows** = la cible ; Linux expérimental (Wine 11.9+, `dotnet48`, `d3dcompiler_47`, `dxvk`, `corefonts`, `sapi` pour Speaker.bot ; script d'installation officiel en GPL-3.0)[22][23] ; macOS expérimental, dépôt Whisky en maintenance et guide limité à la ligne 0.2.8[24] |
| Mise à jour | Auto-updater interne, canal stable par défaut, beta via le Discord, alpha via Patreon[21] |
| Modèle économique | Gratuit ; depuis 1.0.0, des **perks de soutien Patreon** au palier le plus bas (sauvegardes cloud, webhooks custom, limites relevées sur les Decks)[19] |
| Observabilité | Les fenêtres de chat/événements intégrées reposent sur WebView2 (absentes sous macOS/Wine)[24] ; `GetInfo` renvoie `mode`, `os`, `version` de l'instance[2] |
| Adoption | ~32 000 membres sur le Discord officiel et 129+ extensions communautaires, d'après deux extraits de recherche non vérifiés sur les pages[38][39] |

## 4. Les surfaces d'interaction, en détail

### 4.1 Vue d'ensemble

```
                 ┌─────────────────────── Streamer.bot (Windows) ───────────────────────┐
                 │  triggers (444 types) ─► actions (444 types de sub-actions)           │
                 │        ▲                        │                                     │
                 │        │                        ▼                                     │
                 │   C# in-app (~175 méthodes cœur, HTTP client, DLLs)                   │
                 └───┬───────────────┬───────────────┬───────────────┬──────────────────┘
                     │               │               │               │
      serveur WS ◄───┘    serveur HTTP        serveur UDP      client WS SORTANT  +  serveur WS « custom »
      8080, 22 requêtes    7474, POST /DoAction   DoAction        (vers un serveur     (des clients se
      auth optionnelle     réponse 204 nu       sans réponse      tiers, ex. nous)     connectent, messages
                                                                                       visibles en triggers)
```

### 4.2 Serveur WebSocket — la surface principale

- Adresse par défaut `127.0.0.1:8080`, endpoint `/`, démarrage automatique, **liaison locale
  uniquement** sauf si l'opérateur met `0.0.0.0`[3].
- Requêtes et réponses en JSON sur une même connexion : `{"request": "<nom>", "id": "<id>"}` →
  `{"status": "ok"|"error", "id": "<id>"}` ; l'`id` est libre et sert à apparier[5].
- **Les événements ne partent jamais sans abonnement** : il faut envoyer `Subscribe` avec des
  `catégorie: [noms]`, ou `UnSubscribe` ; `GetEvents` liste ce qui est abonnable sur l'instance[2].
- Les 22 requêtes documentées[2] :

| Requête | Ce qu'elle fait | Lecture ou écriture |
| --- | --- | --- |
| `Subscribe` / `UnSubscribe` / `GetEvents` | abonnement aux événements, liste des événements disponibles | lecture |
| `GetInfo` | `instanceId`, `name`, `version`, `os`, `mode`, `source` | lecture |
| `GetBroadcaster` | comptes diffuseur/robot par plateforme (twitch, youtube, kick), connectés ou non | lecture |
| `GetActiveViewers` | **présence** : id, login, rôle, abonné, groupes, canal points dépensés, dernière activité | lecture |
| `GetActions` | actions de l'instance : `id`, `name`, `group`, `enabled`, `subaction_count`, `trigger_count` | lecture |
| `DoAction` | exécute une action, par `id` ou par `name`, avec `args` (dictionnaire) | écriture |
| `GetCodeTriggers` / `ExecuteCodeTrigger` | liste et déclenche les triggers de code | lecture / écriture |
| `GetCommands` | commandes définies (mots-clés, groupes, sensibilité à la casse) | lecture |
| `GetCredits`, `TestCredits`, `ClearCredits` | système de crédits intégré (follows, subs, bits, raids, redemptions, hype trains, éditeurs, modérateurs, groupes, classements) | lecture (+ test/écriture) |
| `GetGlobals`, `GetGlobal`, `TwitchGetUserGlobals`, `TwitchGetUserGlobal` | variables globales persistées ou temporaires, et variables **par utilisateur** | lecture |
| `SendMessage` | envoi dans le chat du diffuseur (`platform`, `bot`, `internal`, `message`) — **la seule requête privilégiée** | écriture |
| `TwitchGetEmotes` / `YouTubeGetEmotes` | emotes (natives, BTTV, FFZ, 7TV) | lecture |
| `GetUserPronouns` | pronoms d'un utilisateur (cache) | lecture |

- **Authentification** : ajoutée en v0.2.5, optionnelle, désactivée par défaut ; quand elle est active,
  le message `Hello` porte un `salt` et un `challenge`, et le client renvoie
  `SHA256_base64(SHA256_base64(motdepasse + salt) + challenge)`. Par défaut, seul `SendMessage` exige
  l'authentification ; une option *Enforce* l'impose à toutes les requêtes[4].
- **Ce qu'on peut en faire sans aucune credential Twitch** : si l'utilisateur a déjà configuré Twitch
  dans Streamer.bot, notre compagnon peut lire le chat, la présence, les crédits et les variables **en
  se contentant du mot de passe du serveur WebSocket** (ou de rien, sauf pour l'envoi). C'est le gain
  le plus net de tout ce document.

### 4.3 Serveur HTTP

- Désactivé par défaut ; `127.0.0.1:7474`[8].
- `POST /DoAction`, corps JSON identique en esprit à la requête WebSocket (`action.id` ou `action.name`,
  `args`), réponse **`204 No Content`** : aucune information d'exécution[9].
- Autres requêtes : `GetActions`, `GetCredits`, `TestCredits`, `ClearCredits`, `ClearFirstWordsCache`[1].
- Peut aussi **servir des fichiers locaux** via des mappings de chemins (ajouté en 1.0.0)[19][42]. Un
  moyen détourné de faire lire un fichier à une page web locale.
- Intérêt pour nous : un chemin d'appel **une seule requête HTTP** quand un client WebSocket permanent
  n'est pas souhaitable (script, cron). Le coût : zéro retour, et il faut lire l'action côté
  Streamer.bot pour savoir ce qui s'est passé.

### 4.4 Serveur UDP

- Une seule requête documentée : `DoAction`[10]. Pas de réponse, pas d'abonnement. Utile pour un tir
  « au mieux » depuis un processus qui ne veut pas de connexion durable ; inutilisable comme source
  d'événements.

### 4.5 Client WebSocket sortant et serveur WebSocket « custom » — Streamer.bot pousse vers nous

Deux mécanismes distincts, tous deux dans la page Servers/Clients[29][30][40] :

- **Client(s) WebSocket** : Streamer.bot **se connecte** à un serveur tiers (`wsUrl`, `wsHost`, `wsPort`,
  `wsPath`, `wsQuery`, nom libre[40]), et un trigger *Websocket Client Message* expose le message reçu
  (`message`, plus les variables du client) aux actions[29]. C'est le chemin « Streamer.bot → nous »
  **sans que nous exposions quoi que ce soit de plus qu'un serveur WebSocket**, et il fonctionne aussi
  par-dessus un VPN/Tailscale.
- **Serveur(s) WebSocket custom** : l'inverse — Streamer.bot écoute, des clients s'y connectent, et
  les triggers *Connection Opened / Message / Connection Closed* rendent les messages visibles[30].
  C'est ainsi que les overlays HTML et Streamer.bot Chat fonctionnent.

Asymétrie de sécurité à retenir : notre module `proxy`/`agent_link` connaît déjà un **protocole JSON
avec jeton d'appairage** ; le serveur custom de Streamer.bot, lui, n'a pas d'authentification documentée
sur cette page. Selon le sens du flux, l'un ou l'autre est le bon choix (section 7).

### 4.6 C# dans l'application

- Un sub-action *Execute C# Code* compile une classe `CPHInline` avec une méthode `Execute()` ;
  `return false` **interrompt les sub-actions suivantes** de l'action courante[14].
- ~175 méthodes « cœur » + ~69 OBS + ~43 intégrations + ~35 Elgato Wave Link + ~28 Kick + ~9 Meld
  Studio[13] : envoi de messages, clips, modération, rewards, scènes et sources, variables, groupes,
  timers, WebSocket (`WebsocketConnect`, `WebsocketSend`, serveurs custom), `TriggerEvent` pour les
  événements custom[13].
- Deux usages pour nous : (a) côté utilisateur, écrire une **petite action d'adaptation** qui fait le
  POST/la réponse que Streamer.bot ne sait pas faire seul[27] ; (b) comprendre que **ce que Streamer.bot
  peut faire est en pratique sans limite** dès que l'utilisateur accepte d'y coller du C#.
- `Fetch URL` (sub-action réseau) ne fait **que du GET** — la documentation renvoie explicitement vers
  C# pour POST[26], et l'exemple officiel utilise `HttpClient` et `Newtonsoft.Json`[27]. À noter : notre
  note interne de juillet 2026 disait que `System.Net.Http` et `Newtonsoft.Json` manquaient dans le bac
  à sable C# ; la documentation de la ligne 1.0.x les documente tous les deux en usage normal. À
  revérifier sur une instance réelle (section 9), mais la contrainte « pas de POST depuis Streamer.bot »
  ne vaut plus.

## 5. Ce que Streamer.bot apporte que le compagnon n'a pas

| Ce que le compagnon a déjà | Ce que Streamer.bot ajoute, déjà configuré chez l'utilisateur |
| --- | --- |
| `twitch`, `kick`, `youtube` : lecture chat + envoi, EventSub, remplacement du payload v2 | 139 types d'événements Twitch, 31 YouTube, 21 Kick exposés **en une seule connexion** (follow, sub, gift, raid, hype train, reward redemption, sondes, prédictions, charité, shield mode, shared chat…)[6] |
| `users`, `viewer_memory`, `chat_context` | présence temps réel (`GetActiveViewers`), **système de crédits** intégré (bits, subs, redemptions, classements), groupes d'utilisateurs, variables globales et **par utilisateur**, commandes définies, quotes, emotes, pronoms[2] |
| `stream_control` (scène, filtre, sondage) via OBS/plateformes | 444 types de sub-actions : OBS (scènes, sources, filtres, médias, replay), Streamlabs Desktop, Meld Studio, clips, marqueurs, modération, rewards, sondages/prédictions, raids, Stream Deck, Wave Link, VTube Studio, Voicemod, sons, fichiers, signaux locaux, TTS Speaker.bot, `Run a Program`[11][41] |
| `audio_output` (TTS maison) | Speaker.bot, application séparée, avec son **propre serveur WebSocket** (`Speak`, `Stop`, `Enable`/`Disable`, `Mode`, `Pause`/`Resume`/`Clear`) et un serveur UDP[33] |
| `capture`, `watch` (captures d'écran) | intégrations caméra/Elgato et screenshots côté Streamer.bot[11] |
| Autorisation default-deny, budgets, résultats terminaux | rien d'équivalent : Streamer.bot **n'a pas de notion d'autorisation par appelant**, et ne confirme pas l'exécution[2][9][37] |

Autrement dit : Streamer.bot est très en avance sur **l'accès aux plateformes et le catalogue d'effets**,
et très en retard sur **les contrats** (autorisation, confirmation, budget). C'est exactement la
complémentarité à exploiter — nous apportons la politique et la preuve, il apporte les bras.

## 6. Contraintes et pièges, sans enrobage

1. **Aucune confirmation d'exécution** en général. `DoAction` renvoie `ok` quand la requête est
   acceptée[2] ; le HTTP renvoie `204`[9] ; l'UDP ne renvoie rien[10]. Le précédent d'intégration IA
   l'écrit noir sur blanc dans son README : « Streamer.bot does not report whether an action
   succeeded »[37]. Même une action qui existe finira peut-être sans avoir rien fait (une condition
   interne fausse, un sub-action en échec).
2. **Réponse structurée possible, mais à la charge du client et de l'auteur de l'action** : il faut
   que l'action se termine par *Trigger Custom Event*[28] ; le client officiel corrèle en injectant un
   argument (`sbClientResponse`) et en attendant l'événement `Custom.Event` correspondant, 10 s par
   défaut[15][17]. Si nous voulons ce confort, notre module devra implémenter la même convention — et
   les actions de l'utilisateur devront être écrites pour y répondre.
3. **Files d'actions** : une action peut être en file derrière d'autres, et une file marquée *Blocking*
   impose d'attendre la fin de la précédente[31][32]. Un `DoAction` peut donc ne rien faire **tout de
   suite**, voire jamais, sans que personne ne l'apprenne. À traiter comme un délai d'attente, pas
   comme un échec.
4. **`Fetch URL` ne fait que du GET**[26] ; tout POST/PUT/PATCH passe par du C# `HttpClient`[27]. Si
   notre module attend un appel HTTP entrant de Streamer.bot, l'utilisateur devra coller du C# (ou
   passer par le **client WebSocket sortant**, qui lui ne demande aucun code[29]).
5. **Nature de la distribution** : binaire fermé, sans licence sur le dépôt[36] ; perks payants depuis
   1.0.0 (sauvegardes cloud, webhooks custom)[19]. Ce n'est pas un obstacle technique, mais ça
   interdit de considérer Streamer.bot comme un composant qu'on peut patcher ou livrer avec nous.
6. **Windows d'abord** : Linux (Wine) et macOS sont explicitement expérimentaux et sans support[22][24].
   Chez l'ami de l'utilisateur, Streamer.bot tourne sur Windows — c'est un prérequis de fait de
   l'intégration, pas un détail.
7. **Kick est fragile** : supporté depuis 1.0.0 seulement, par webhooks, et l'éditeur signale lui-même
   des délais de livraison côté Kick et une limite d'abonnements webhook à faire relever auprès de
   Kick[20][25]. Ne pas bâtir la démonstration multi-plateforme dessus.
8. **Accès distant** : il faut changer l'adresse d'écoute (`0.0.0.0` ou l'IP LAN) pour sortir de la
   machine[3][7] ; depuis une page servie en HTTPS, `ws://` non-localhost est bloqué par les
   navigateurs (mixed content), et la recette officielle passe par **Tailscale Serve** en `wss://`[7].
   Si le compagnon tourne sur un autre hôte que Streamer.bot (topologie « cerveau distant » de
   design-v2), c'est ce chemin qu'il faut prévoir — ou le client WebSocket sortant de Streamer.bot.
9. **Authentification faible par défaut** : désactivée, et seul `SendMessage` est privilégié[4]. Un
   serveur WebSocket localhost sans mot de passe est accessible à tout processus local.
10. **Pas de SDK de plugin documenté** : l'extension se fait par C# dans l'application, ou par les
    surfaces externes de la section 4. Aucune page de la documentation consultée ne décrit d'API
    d'extension tierce[13][14]. Les « extensions » du catalogue communautaire sont des paquets
    d'actions importables et des serveurs externes.
11. **Le client Python existant est neuf et non éprouvé** : `pystreamerbot`, MIT, créé le 15 août 2026,
    0 étoile, inspiré de l'architecture discord.py[34]. Utile comme référence de lecture, pas comme
    dépendance. La référence à lire est le **client officiel TypeScript** (MIT, 83 étoiles, dernier
    envoi juillet 2026), qui documente les requêtes et implémente la corrélation des réponses[15][16][17] ;
    notre `aiohttp` déjà présent suffit à écrire notre propre client (section 8).
12. **Chiffres d'adoption non vérifiés** : le Discord à ~32 000 membres et les « 129+ extensions » sont
    des extraits de recherche[38][39], pas des mesures.

## 7. Trois architectures d'intégration

### A. Streamer.bot comme **adaptateur de plateformes** (source)

```
Streamer.bot ──(WS : Subscribe Twitch.ChatMessage, Follow, RewardRedemption…)──► module `streamerbot`
                                                                                  │ publie des
                                                                                  ▼ événements bus v2
                                                    admission ─► brain ─► exécuteur ─► …
```

- Avantage : **zéro credential à collecter** au-delà du mot de passe du serveur WS ; l'utilisateur garde
  sa configuration ; on hérite de 139+31+21 types d'événements d'un coup[2][6].
- Coût : il faut écrire le **mapping événement Streamer.bot → payload v2** du compagnon (et donc le
  tester), la source de vérité du chat devient Streamer.bot (décalage, dépendance à sa connexion), et
  les triggers/politiques du compagnon s'appliquent à des événements qui ne sont plus produits par nos
  modules `twitch`/`kick`/`youtube` — deux chemins concurrents pour la même donnée, à interdire par
  configuration explicite.
- Sûreté : lecture seule côté plateformes (aucun `SendMessage`, donc aucune authentification
  nécessaire) ; **le module ne déclare aucune action** s'il n'est que source.

### B. Streamer.bot comme **exécuteur d'actions** (puits)

```
brain ─► exécuteur (default-deny) ─► module `streamerbot` ──DoAction(action, args)──► Streamer.bot ─► OBS,
                                                                                     plateformes, Stream Deck…
                                                                                     (silence : aucun retour)
```

- C'est le gain immédiat : « fais tourner l'action *Passer en scène BRB* de mon Streamer.bot » donne
  accès à 444 types de sub-actions avec **une seule action chez nous**, et sans réimplémenter OBS,
  Stream Deck, Wave Link, VTube Studio…[11].
- Contrat à respecter : `DoAction` est une écriture **non confirmée**[2][37] → notre action aurait
  `nature: write`, `idempotency: none`, et un résultat terminal honnête : `accepted` si la requête est
  acceptée puis rien, ou `external_unknown` si l'on veut exiger une confirmation qu'on n'aura pas. Le
  vocabulaire existe déjà chez nous : `clips` rend `uncertain`/`external_unknown` quand la confirmation
  est perdue, et l'exécuteur ne réessaie jamais aveuglément.
- Contrôle d'accès obligatoire côté nous : une **liste blanche d'actions Streamer.bot** nommées par
  l'opérateur (nos `supported_destinations` + règles `actions:` du profil), parce que Streamer.bot
  n'authentifie pas le demandeur et n'a aucune notion d'autorisation[4].
- Variante B' : ajouter le **round-trip** via *Trigger Custom Event*[28] quand l'action le supporte —
  utile pour les rares cas où l'on veut une valeur de retour (par ex. `GetActiveViewers`-like déjà
  couvert par la requête de lecture). À réserver aux actions que l'utilisateur accepte d'adapter.

### C. Streamer.bot comme **déclencheur du compagnon** (Streamer.bot → nous)

```
évent Streamer.bot ─► action SB (sub-action : client WS / Run a Program / C# HttpClient) ──► notre `proxy`
                                                                                             (WS JSON + jeton
                                                                                             d'appairage, déjà livré)
```

- Avantage : réutilise notre **protocole proxy** (documenté, jeton, une connexion par cerveau) au lieu
  d'inventer un canal[29][41] ; c'est l'utilisateur avancé qui route un événement de son choix vers
  nous, sans que le compagnon ait à s'abonner à tout.
- Coût : c'est de la configuration manuelle chez l'utilisateur, et le POST HTTP direct depuis Streamer.bot
  demande du C# (contrainte 4)[26][27] — le **client WebSocket sortant** de Streamer.bot évite ce
  C#[29].
- À noter : ce chemin ne dépend pas de notre serveur d'écoute, donc il fonctionne aussi quand le
  compagnon est derrière un NAT, à condition que Streamer.bot sache sortir.

### Recommandation

**B d'abord, C ensuite, A seulement si un utilisateur le demande explicitement.**

Raisons : B est la plus petite surface utile (une action, une liste blanche, un contrat honnête) et
elle exploite ce que Streamer.bot a de meilleur (les effets) sans toucher à notre chaîne d'entrée ni à
notre payload. C se branche sur un protocole que nous avons déjà écrit et testé. A est séduisant mais
coûteux : il faut un mapping d'événements complet, il duplique nos trois modules de plateforme, et il
introduit une dépendance à Streamer.bot là où notre valeur est justement l'autonomie.

## 8. Esquisse de module `streamerbot`

Pour la variante B (et C côté client WebSocket), un module `modules/streamerbot/` de notre format
courant (manifest_version 2, même vocabulaire que `stream_control`/`clips`) :

```yaml
name: streamerbot
manifest_version: 2
runtime_api: 2
produces: []                       # rien sur le bus : c'est un puits
consumes: []                       # il est appelé par l'exécuteur, pas par un événement
middleware: false
lifecycle:
  roles:
    - output                       # puits : pas de rôle input, ni drain d'événements
settings_schema:
  title: Streamer.bot settings
  type: object
  properties:
    websocket_url: {type: string}      # ws://127.0.0.1:8080/
    websocket_password: {type: string} # référence d'environnement, jamais un littéral
    allowed_actions: {type: array, items: {type: string}}  # liste blanche des noms d'actions
    request_timeout_seconds: {type: number}
  required: [websocket_url, allowed_actions]
settings_validator: validate_settings
credentials: [websocket_password]
actions:
  - name: streamerbot.action.run
    version: 1
    description: Exécute une action nommée de l'instance Streamer.bot.
    argument_schema:
      type: object
      properties:
        action: {type: string}
        args: {type: object}
      required: [action]
      additionalProperties: false
    result_schema:
      type: object
      properties:
        accepted: {type: boolean}
        request_id: {type: string}
      required: [accepted, request_id]
      additionalProperties: false
    nature: write
    required_permissions: [streamerbot.action]
    supported_destinations:
      - platform: "*"
        channel_id: "*"
        scope: streamerbot
    timeout_seconds: 10
    idempotency: none              # aucune garantie de non-répétition côté Streamer.bot
    delivery:
      text_argument: none          # l'action est nommée par les arguments de livraison
```

Points de conception à ne pas manquer :

1. **La liste blanche est obligatoire** (`allowed_actions`) : sans elle, l'action `streamerbot.action.run`
   serait un « exécute n'importe quoi chez l'utilisateur », ce qui est exactement ce que le
   default-deny du compagnon interdit. Un nom hors liste est refusé **avant** toute requête[2][4].
2. **Résultat honnête** : `accepted: true` signifie « Streamer.bot a accepté la requête », jamais
   « l'action a réussi ». Le `description` de l'action doit le dire, comme le fait déjà l'action
   `stream.clip.create` pour ses réponses incertaines.
3. **Timeout court et fixe** : au-delà, la réponse à `DoAction` ne viendra peut-être jamais (files
   bloquantes[31][32]) ; le plafond du compagnon (10 s par défaut dans les profils) est un bon point de
   départ.
4. **Pas de reconnexion silencieuse** dans le contrat : un WS coupé doit rendre la capacité
   indisponible (le module n'est plus *ready*), ce que l'exécuteur traduit déjà par
   `refused provider_not_ready` — l'état exact qu'on veut afficher au modèle.
5. **Lecture** : `GetActiveViewers`, `GetCredits`, `GetGlobals` sont des requêtes de lecture utiles et
   non privilégiées[2] ; elles mériteraient un second module (ou des actions `nature: read` dans le
   même module) plutôt que d'être mélangées à l'écriture.
6. **Le mot de passe est un secret** : il suit la règle du dépôt — `${SB_WEBSOCKET_PASSWORD}` dans le
   profil, redaction dans les traces, et jamais affiché par l'UI de configuration[3][4].

## 9. Ce qu'il faut mesurer avant de s'engager

Aucune de ces questions n'est tranchée par la documentation, et toutes changent la conception :

1. `DoAction` **par nom** sur une action inexistante : `status: error` ou `ok` silencieux ? (décide si
   la liste blanche peut être vérifiée au démarrage via `GetActions`.)
2. Latence réelle d'un aller-retour `DoAction` sur une instance chargée, et comportement sous file
   bloquante[31][32] : combien de temps avant de considérer qu'on ne saura jamais ?
3. Comportement du serveur WS avec `Enforce` activé : la connexion échoue-t-elle proprement (ce que
   notre coordination de cycle de vie sait traiter) ou reste-t-elle ouverte sans droits ?
4. Version **minimale** pour `GetActiveViewers`/`GetCredits`/`ExecuteCodeTrigger` : la documentation ne
   date pas ces requêtes[vérifier sur l'instance], or le parc installé n'est pas forcément en 1.0.
5. `System.Net.Http` + `Newtonsoft.Json` disponibles dans le C# de la ligne 1.0.x (notre note de juillet
   2026 disait l'inverse)[27][36].
6. Round-trip `Custom.Event` réellement corrélé quand l'action se termine par *Trigger Custom Event*[17][28].
7. Sous Wine (si l'on veut un jour tester côté Linux) : les serveurs WS/HTTP de la 1.0.5+ sur Kestrel
   démarrent-ils[18][22] ?
8. Volume d'événements d'un vrai stream sur `Subscribe` : 139 types Twitch abonnés d'un coup, est-ce
   soutenable pour notre file d'admission bornée[6] ?

Un « enregistrement d'essai » (au sens de `docs/campaigns/phase2/scaffolding/obs-trial-record.md`)
devrait consigner ces huit points, avec la version exacte de l'instance et la date — c'est ce que le
dépôt demande avant d'écrire « livré ».

## 10. Décisions à trancher

1. **Périmètre de la première intégration** — a) variante B seule (exécuter des actions Streamer.bot
   depuis le compagnon) ; b) B + C (Streamer.bot peut aussi déclencher le compagnon via le proxy) ;
   c) A d'abord (Streamer.bot comme source de chat) ; d) étude seule, on ne code rien encore.
2. **Liste blanche ou inventaire dynamique** — a) noms d'actions déclarés en configuration, refus
   sinon ; b) `GetActions` au démarrage, puis les actions de l'instance deviennent disponibles
   automatiquement (moins sûr, plus pratique) ; c) hybride : inventaire affiché dans l'UI de config,
   sélection explicite stockée.
3. **Round-trip** — a) aucun (fire-and-forget assumé et documenté) ; b) `Custom.Event` obligatoire pour
   les actions de la liste blanche (l'utilisateur doit adapter ses actions) ; c) par action, selon un
   drapeau de configuration.
4. **Topologie** — a) Streamer.bot et compagnon sur la même machine (localhost, cas de l'ami) ;
   b) cerveau distant, client WS sortant de Streamer.bot vers notre proxy ; c) les deux, adressées par
   configuration.
5. **Lecture** — a) module d'écriture seul pour commencer ; b) écriture + lecture (`GetActiveViewers`,
   `GetCredits`, `GetGlobals`) dans le même module ; c) deux modules séparés (un puits, une source).
6. **Avant tout code** — a) essai réel sur l'instance de l'ami avec un enregistrement d'essai (les huit
   points de la section 9) ; b) développement contre un faux serveur WS d'abord, essai réel ensuite ;
   c) attendre que l'utilisateur tranche 1 à 5.

## Sources

[1] https://docs.streamer.bot/api/websocket — Streamer.bot Docs - WebSocket Server (vue d ensemble des evenements et requetes)
[2] https://docs.streamer.bot/api/websocket/requests — Streamer.bot Docs - WebSocket Requests (les 22 requetes et leurs schemas)
[3] https://docs.streamer.bot/api/websocket/guide/configuration — Streamer.bot Docs - Configuration du serveur WebSocket (127.0.0.1:8080, endpoint /, auth, enforce)
[4] https://docs.streamer.bot/api/websocket/guide/authentication — Streamer.bot Docs - Authentification (ajoutee en v0.2.5, salt/challenge SHA256, SendMessage privilegie)
[5] https://docs.streamer.bot/api/websocket/guide/requests — Streamer.bot Docs - Envoi de requetes (schema requete/reponse JSON)
[6] https://docs.streamer.bot/api/websocket/events — Streamer.bot Docs - WebSocket Events (45 categories, Twitch 139, Elgato 131, YouTube 31, Kick 21)
[7] https://docs.streamer.bot/api/websocket/recipes/remote-access — Streamer.bot Docs - Acces distant (LAN, 0.0.0.0, restrictions mixed content, Tailscale Serve)
[8] https://docs.streamer.bot/api/http/guide/configuration — Streamer.bot Docs - Configuration du serveur HTTP (127.0.0.1:7474, auto-start desactive)
[9] https://docs.streamer.bot/api/http/requests/do-action — Streamer.bot Docs - HTTP POST /DoAction (reponse 204 No Content)
[10] https://docs.streamer.bot/api/udp/requests/do-action — Streamer.bot Docs - UDP DoAction
[11] https://docs.streamer.bot/api/sub-actions — Streamer.bot Docs - Reference des sub-actions (444 entrees, core/elgato/integrations/kick/meld/obs/polypop/settings/speakerbot/streamerbot/streamlabs/twitch/youtube)
[12] https://docs.streamer.bot/api/triggers — Streamer.bot Docs - Reference des triggers (444 entrees, meme arborescence)
[13] https://docs.streamer.bot/api/csharp/methods — Streamer.bot Docs - Methodes C# (Core 175, Obs Studio 69, Integrations 43, Elgato 35, Kick 28, Meld 9)
[14] https://docs.streamer.bot/api/csharp/guide/intro — Streamer.bot Docs - Introduction au C# (classe CPHInline, Execute(), return false arrete les sub-actions)
[15] https://streamerbot.github.io/client/guide/actions — Streamer.bot Client - Actions (getActions, doAction, arguments, customEventResponse depuis @streamerbot/client v1.10.0)
[17] https://raw.githubusercontent.com/Streamerbot/client/main/packages/client/src/ws/StreamerbotClient.ts — Streamerbot/client - StreamerbotClient.ts (doActionWithCustomEventResponse, argument sbClientResponse, timeout 10s)
[18] https://streamer.bot/api/releases/streamer.bot — streamer.bot - API des releases (canal stable : 1.0.7, notes de la ligne 1.0.5)
[19] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/5.changelogs/.archive/v1.0.0.md — Streamer.bot v1.0.0 - notes de version (Kick, refonte UI WPF, Decks 2.0, .NET 4.8.1, perks Patreon : backups cloud et webhooks custom)
[20] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/5.changelogs/.archive/v1.0.1.md — Streamer.bot v1.0.1 - notes de version (hotfix ; limite d abonnements webhook cote Kick)
[21] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/1.get-started/1.installation/0.index.md — Streamer.bot Docs - Installation Windows (application portable, auto-updater, canaux stable/beta/alpha)
[22] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/1.get-started/1.installation/1.linux/0.index.md — Streamer.bot Docs - Installation Linux (experimentale, Wine 11.9+, dotnet48, d3dcompiler_47, dxvk, corefonts, sapi)
[23] https://github.com/Streamerbot/sb-linux-installer — GitHub Streamerbot/sb-linux-installer - script d installation Linux (GPL-3.0, 37 etoiles, dernier push 2026-05-06)
[24] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/1.get-started/1.installation/2.macos.md — Streamer.bot Docs - Installation macOS (experimentale, Whisky en maintenance, guide limite a la ligne 0.2.8)
[25] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/faq/27.platform-support.md — Streamer.bot Docs - Support des plateformes (Kick uniquement en 1.0.0 et via webhooks, TikTok non, TikFinity en integration)
[26] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/1.sub-actions/core/network/fetch-url.md — Streamer.bot Docs - sub-action Fetch URL (GET uniquement, renvoi vers C# pour POST)
[27] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/4.examples/http-post.md — Streamer.bot Docs - exemple POST/PUT/PATCH via HttpClient en C# (Newtonsoft.Json)
[28] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/1.sub-actions/core/triggers/custom-event-trigger.md — Streamer.bot Docs - sub-action Trigger Custom Event (Evenement/Evenement code, Use Args)
[29] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/2.triggers/core/websocket/client/websocket-client-message.md — Streamer.bot Docs - trigger Websocket Client Message (un message recu d un serveur WebSocket externe)
[30] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/2.triggers/core/websocket/custom-server/message.md — Streamer.bot Docs - trigger WebSocket Custom Server Message (message recu d un client connecte au serveur custom de SB)
[31] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/2.guide/01.core/01.actions.md — Streamer.bot Docs - guide Actions (actions, sub-actions, pile d arguments, files d actions)
[32] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/faq/09.action-blocking.md — Streamer.bot Docs - FAQ file d actions bloquante (une action n attend la fin de la precedente que si la file est marquee Blocking)
[33] https://raw.githubusercontent.com/Streamerbot/docs/main/speakerbot/2.api/0.websocket/1.requests.md — Speaker.bot Docs - requetes du serveur WebSocket (Speak, Stop, Enable/Disable, Mode, Pause/Resume/Clear)
[34] https://github.com/miichom/pystreamerbot — GitHub miichom/pystreamerbot - client Python communautaire (MIT, 0 etoile, cree 2026-08-15, dernier push 2026-09-05)
[35] https://api.github.com/orgs/Streamerbot/repos — API GitHub - depots de l organisation Streamerbot (Streamer.bot, client, docs, sb-linux-installer, streamerbot-wiki, TwitchSpeaker)
[36] https://api.github.com/repos/Streamerbot/Streamer.bot — API GitHub - depot Streamerbot/Streamer.bot (aucune licence, aucun langage, aucune release : le binaire est distribue hors GitHub)
[37] https://github.com/sandraschi/streamfog-mcp — GitHub sandraschi/streamfog-mcp - precedent d integration IA : serveur MCP qui traduit des appels d outils en DoAction Streamer.bot, avec l avertissement fire-and-forget
[38] https://discord.com/invite/hYqQGG3puC — Discord officiel Streamer.bot - environ 32 000 membres (chiffre vu en extrait de recherche, non verifie sur la page)
[39] https://contentcreators.com/tools/streamerbot-review — Content Creators - revue Streamer.bot (129+ extensions communautaires ; extrait de recherche, non verifie sur la page)
[40] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/.variables/websocket-client/WebsocketClient.md — Streamer.bot Docs - variables du client WebSocket (wsName, wsUrl, wsHost, wsPort, wsPath, wsQuery)
[41] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/1.sub-actions/core/system/run-a-program.md — Streamer.bot Docs - sub-action Run a Program
[42] https://raw.githubusercontent.com/Streamerbot/docs/main/streamerbot/3.api/5.http/0.guide/2.serving-files.md — Streamer.bot Docs - servir des fichiers locaux via le serveur HTTP (mappings de chemins)
