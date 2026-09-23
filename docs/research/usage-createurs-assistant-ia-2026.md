# Ce que les streamers utilisent et demandent à un assistant IA — recherche, septembre 2026

Recherche menée pour décider de la suite du projet (`twitch-ia-compagnon`), après la clôture des phases 0–2.
Objectif : partir de l'**usage réel** et des **demandes réelles**, pas d'intuitions.

## 1. Résumé exécutif

- Le marché des co-hosts IA est **déjà occupé** mais par des produits **fermés, mensualisés et hébergés** :
  ai_licia (3,99–24,99 $/mois selon le nombre de streams et de personnages)[5], Questie (50 000 utilisateurs
  revendiqués)[8], StreamChat AI (11 500 streamers)[9], plus le « Intelligent Streaming Agent » de Streamlabs
  (avatars 3D, gestion de scènes)[10].
- Ce que ces produits **vendent** se résume à quatre promesses : **remplir le silence** (dead air)[8], **ne pas
  casser le rythme** (automatisation de production)[10], **faire vivre le chat** (engagement, mémoire)[4][9] et
  **ne plus être seul** (charge mentale, burnout)[15][10].
- La **plateforme bouge aussi** : Twitch teste « Stream Coach », un coaching **post-stream** dans le Creator
  Dashboard[11] — accueilli avec **défiance** par une partie des créateurs, qui reprochent le flou sur
  l'entraînement et l'usage de leurs données[12].
- Sur 1 183 retours communautaires analysés[1][2], la **majorité des douleurs est de niveau plateforme**
  (découverte, notifications, concurrence) : hors de notre portée. Mais plusieurs intentions récurrentes sont
  **exactement dans notre périmètre** : clips/highlights (63 items côté créateur), accueil et engagement (43),
  alertes et overlays (33), TTS/voix (26), bots et automatisation (21)[2][3].
- **Conséquence stratégique** : notre différenciateur n'est pas « avoir un co-host IA » (le marché en a), c'est
  **la configabilité, le local et le contrôle** — le compagnon tourne chez le streamer, avec des règles
  default-deny, des budgets bornés et une mémoire effaçable, là où l'offre actuelle est un SaaS fermé[4][5][12].

## 2. Méthode

1. **Analyse quantitative d'un dataset public** : 1 183 retours (r/Twitch, r/streaming, r/Twitch_Startup,
   Hacker News, stores), collectés sur 12 mois, chaque item lié à son URL source[1][2] ; snapshot de fréquences
   par thème et répartition créateur/viewer[3]. J'ai ré-analysé le brut localement par intentions
   (« catégories adressables ») plutôt que par thèmes du dataset.
2. **Relevé de l'offre** : pages produit et tarifs des concurrents (ai_licia[4][5], Questie[8], StreamChat
   AI[9]) et par deux panoramas 2026[13][14]. Une analyse de marché complète le tableau[10].
3. **Direction plateforme** : Twitch Stream Coach[11] et la réaction des créateurs[12].
4. **Restitution** : cartographie feature → module nécessaire → effort → conflit éventuel avec les contraintes
   du projet (default-deny, captures à la demande, rétention bornée).

## 3. Ce que les créateurs demandent réellement

### 3.1 Les douleurs principales sont plateforme (et hors de notre périmètre)

Fréquences du snapshot, tous items confondus[3] : outils communautaires 734, découverte 448 (sévérité 5),
concurrence entre plateformes 298, burnout et churn créateur 157. Les thèmes de notification (live raté,
fatigue, push mobile) sont réels mais faibles en volume (8–17 chacun)[3]. Ces sujets — algorithmes,
notifications, exode vers Kick/YouTube — **ne se règlent pas avec un compagnon IA local**.

### 3.2 Les intentions adressables, mesurées sur le brut[2]

| Intention (items où elle apparaît) | Total | dont perspective « créateur » |
|---|---|---|
| Analytics / croissance | 151 | 48 |
| **Clips / highlights / VOD** | 132 | **63** |
| IA / LLM (souvent « l'IA me ferait gagner du temps ») | 94 | 19 |
| Overlays / alertes | 88 | 33 |
| **Accueil / engagement / raids** | 86 | **43** |
| **TTS / voix** | 51 | 26 |
| **Bots / automatisation** | 38 | 21 |
| **Modération / toxicité** | 32 | 11 |
| Traduction / multilinguisme | 22 | 5 |
| Agenda / multistream | 16 | 13 |
| Voix clonée / personnage | 12 | 6 |

Lecture honnête : ce sont des **occurrences**, pas des votes ; un item peut relever de plusieurs intentions, et
certains matches sont approximatifs. La tendance reste nette : **après le post-stream (clips), ce qui revient
est la relation avec les gens présents (accueil, engagement, souvenirs) et la voix.**

### 3.3 Ce que dit le discours de marché

- « **Dead air is a stream killer** » : les viewers partent vite quand il ne se passe rien — argument central
  des co-hosts qui **regardent** le gameplay et le commentent[8].
- « **Tool overload** » : le streamer de 2026 subit la pression d'empiler overlay, alertes, bot, modérateurs,
  calendrier, clips, shorts[6] ; la promesse gagnante est la **consolidation**, pas un outil de plus.
- **Charge mentale et burnout** : répondre aux messages, gérer la communauté et modérer les conflits même
  épuisé est une cause directe d'abandon[15].
- **Mémoire et personnalité** : les produits mettent en avant la mémoire long-terme et le personnalisable
  (« elle se souvient »)[4][9].
- **La voix** est un axe à part entière : TTS de chat, alertes parlées, voix de personnage[7][17].
- **La confiance est un angle mort du marché** : la fonctionnalité IA de la plateforme est perçue comme
  imposée, avec un flou sur l'entraînement et les données[12].

## 4. L'offre concurrente (état des lieux)

| Produit | Positionnement | Ce qu'il fait | Modèle | Ce qu'il ne fait pas |
|---|---|---|---|---|
| **ai_licia** | Co-host « backstage crew + fan »[4] | Voit le stream, personnalité, mémoire long-terme, voix, **Actions** (clips, scènes OBS, actions en jeu)[7] | Abonnement 3,99–24,99 $/mois, tiers par streams/semaine et personnages[5] | Fermé, hébergé, pas de règles/politiques exposées, pas de self-host |
| **Questie** | Compagnon de jeu qui **regarde** l'écran[8] | Commentaire temps réel, questions aux viewers, personnages | SaaS (50k utilisateurs revendiqués)[8] | Fermé, centré jeu, peu de contrôle du streamer |
| **StreamChat AI** | Chatbot IA + modération[9] | Conversations contextuelles, mémoire, modération, tableau de bord | SaaS (11 500 streamers)[9] | Twitch/Kick seulement, pas de production ni de voix |
| **Streamlabs ISA** | Agent de production[10] | Avatars 3D, **gestion de scènes**, détection de gameplay | Écosystème Streamlabs | Verrouillé à l'écosystème, fermé |
| **Twitch Stream Coach** | Coaching **post-stream**[11] | Feedback personnalisé après chaque live, dans le Dashboard | Gratuit (plateforme), en test | Post-stream seulement, réserves des créateurs[12] |
| **Streamer.bot / Firebot / MixItUp** | Automatisation sans IA | Déclencheurs, actions, OBS, TTS | Gratuit / open source | Pas d'IA, courbe d'apprentissage, Windows surtout[14][16] |

## 5. Cartographie vers notre architecture

Notre design actuel : un **brain agentique** qui ne propose que des **lectures**, un **exécuteur** qui applique
des politiques (default-deny), une **liste de livraison configurable**, des **budgets bornés**, des captures
**à la demande** et un déploiement **PC ou serveur via proxy**. Voici ce que les demandes ci-dessus donnent,
feature par feature.

| # | Feature demandée | Ce qu'il faut | Déjà là ? | Effort | Conflit / remarque |
|---|---|---|---|---|---|
| 1 | Accueil des nouveaux, remerciements subs/follows | `chat.read` + `chat.write` + déclencheurs | **Oui** | Très faible (config + prompt) | Aucun ; c'est du réglage, pas du code |
| 2 | « Qu'est-ce que j'ai raté ? » — résumé du chat | `chat.read` + modèle | **Oui** | Très faible | Borné par la fenêtre de lecture |
| 3 | Répondre/traduire pour les viewers étrangers | `chat.read`/`chat.write` + modèle | **Oui** | Faible | Qualité dépend du modèle |
| 4 | Sondages et votes dans le chat | `stream.poll.create` | **Oui** | Faible | Réconciliation déjà en place |
| 5 | Scènes contextuelles (starting soon, BRB, fin) | `stream.scene.set` | **Oui** | Faible (config + déclencheurs) | OBS requis |
| 6 | Voix du compagnon (TTS) | `audio.speak` | **Oui** | Très faible | Aucun ; voix = configuration |
| 7 | Musique/jingles contextuels | `audio.play` (clips configurés) | **Oui** | Très faible | Aucun |
| 8 | Réagir à ce qui se passe à l'écran | `screen.capture` + prompt | **Oui** | Faible | **Capture à la demande** : pas de veille continue (voir conflits) |
| 9 | Clips automatiques **live** | **Nouveau** : `stream.clip.create` (API plateforme) | Non | Moyen | Demande n°1 côté créateur (63 items)[2] |
| 10 | TTS des messages de chat (file d'attente, redemptions) | **Nouveau** : `tts_queue` (module dédié, parole sérialisée) | Partiel (`audio.speak`) | Moyen | Il faut une **file bornée** et une politique de priorité |
| 11 | Modération assistée (classer, signaler, masquer) | **Nouveau** : `moderation` + `chat.write` | Non | Moyen | Le streamer veut souvent que le **modèle propose**, pas qu'il agisse → décision produit |
| 12 | Mémoire des habitués (« il se souvient de moi ») | **Nouveau** : module mémoire optionnel | Prévu au design | Moyen | Rétention/effacement à fixer (RGPD) |
| 13 | Marqueurs et highlights post-stream | **Nouveau** : `stream.marker` + mémoire | Non | Moyen | Complémentaire aux clips |
| 14 | Résumé post-stream « ce qui a marché » | **Nouveau** : analytics + mémoire | Non | Moyen | Concurrent local de Stream Coach[11] |
| 15 | Repurposing (clips → shorts, titres, descriptions) | **Nouveau** : module publication + mémoire | Non | Moyen–gros | Hors flux live |
| 16 | Multi-plateforme (Kick, YouTube, TikTok) | **Nouveaux adaptateurs** plateforme | Non | Gros | Déjà prévu par l'architecture modulaire |
| 17 | Planning, rappels, annonces Discord | **Nouveau** : `schedule` + `notify` | Non | Moyen | Utile contre le burnout[15] |
| 18 | Assistance technique pré-stream (checklist OBS) | **Nouveau** : diagnostics + `stream.scene.set` | Non | Moyen | Adresse l'anxiété pré-stream[2] |

### Conflits avec les contraintes actuelles (à trancher, pas à ignorer)

- **Écritures proposées par le modèle** : aujourd'hui le brain ne propose que des lectures ; les écritures
  passent par la **liste de livraison** ou un appel d'exécuteur autorisé. La modération automatique et les
  clips déclenchés par le modèle exigeraient de rouvrir cette décision (renvoyée à la phase 3+ dans la spec).
- **Veille continue de l'écran** : le design impose des captures **à la demande**, jamais de fond. Un co-host
  qui « regarde en permanence » comme Questie[8] supposerait un déclencheur dédié, une cadence configurée et
  un budget propre — donc une décision explicite, pas un effet de bord.
- **Mémoire** : le socle est **volontairement volatil et borné** ; la mémoire long-terme doit rester un module
  optionnel, avec durée de rétention et effacement.
- **Hors périmètre assumé** : découverte, notifications de plateforme, algorithmes[3].

## 6. Ce qui est déjà faisable **sans écrire de code**

C'est le résultat le plus actionnable : le « pack présence » (accueil, remerciements, résumé de chat,
traduction, sondages, scènes, voix, jingles, réactions à l'écran) repose **entièrement sur les modules livrés**
(`chat.read`, `chat.write`, `screen.capture`, `audio.speak`, `audio.play`, `stream.scene.set`,
`stream.poll.create`). Il s'agit de **configuration, de déclencheurs et de prompts** — donc d'exemples de
configuration livrés et documentés, pas d'une phase de développement.

## 7. Recommandation pour la phase 3 (ordre proposé)

1. **Pack « présence »** (items 1–8) : le meilleur rapport valeur/effort, quasi entièrement configurable.
   À livrer comme profil d'exemple + documentation, avec un petit test d'intégration.
2. **Clips live** (item 9) : la demande n°1 côté créateur[2] et une primitive manquante ; l'API plateforme
   existe déjà dans le module `twitch`.
3. **Mémoire des habitués** (item 12) : le différenciateur émotionnel mis en avant par les concurrents[4][9],
   mais **local, borné et effaçable**.
4. **Modération assistée** (item 11) : forte demande[9], mais c'est un module à part entière **et** une
   décision sur les écritures automatiques.
5. **Résumé post-stream local** (item 14) : réponse directe à Stream Coach[11] avec l'argument « tes données
   restent chez toi »[12].

## 8. Décisions à trancher

1. **Quelle suite ?** Le pack « présence » seul (config), ou l'un des candidats 2–5 (code) ?
2. **Modération** : le modèle peut-il *proposer* une action de modération (et le streamer/une règle l'applique),
   ou seulement *alerter* ?
3. **Captures** : autorise-t-on un déclencheur « regarde le jeu à cadence réglable » (avec budget dédié), ou
   reste-t-on strictement à la demande ?
4. **Mémoire** : durée de rétention par défaut, et effacement (par viewer ? global ? sur demande ?).
5. **Multi-plateforme** (Kick/YouTube) : maintenant ou après les candidats ci-dessus ?

## Sources

[1] https://github.com/hbschlac/twitch-community-research — Twitch Community Intelligence - 1183 retours publics, 10 themes de douleur
[2] https://raw.githubusercontent.com/hbschlac/twitch-community-research/main/data/raw-feedback.json — Dataset brut 1183 items analyse localement pour cette recherche
[3] https://raw.githubusercontent.com/hbschlac/twitch-community-research/main/data/snapshot.json — Snapshot d analyse - frequences par theme et repartition createur/viewer
[4] https://www.getailicia.com/ai-licia-for-twitch — ai_licia - page produit Twitch (vision, personnalite, memoire, voix)
[5] https://www.getailicia.com/pricing — ai_licia - tarifs 2026 (tiers par streams/semaine et personnages)
[6] https://www.getailicia.com/post/top-5-twitch-streaming-tools-in-2026 — ai_licia - Top 5 Twitch tools 2026 (surcharge d outils)
[7] https://www.getailicia.com — ai_licia - accueil (Actions: clips, scenes OBS, actions en jeu)
[8] https://www.questie.ai/twitch-streamers — Questie AI - page Twitch (co-host qui regarde le gameplay, dead air)
[9] https://contentcreators.com/tools/streamchatai — StreamChat AI - moderation IA + engagement, 11 500 streamers
[10] https://www.jenova.ai/en/resources/ai-streaming-assistant-202605 — Jenova mai 2026 - assistant de production, Streamlabs ASA, marche 4,2-12,3 Md
[11] https://www.tubefilter.com/2026/09/15/twitch-generative-ai-stream-coach-creator-assistant — Tubefilter 15/09/2026 - Twitch Stream Coach, coaching post-stream
[12] https://kotaku.com/twitch-ai-stream-coach-tool-ai-content-training-opt-out-2000733578 — Kotaku 11/09/2026 - defiance des createurs envers l IA de plateforme
[13] https://streamscharts.com/news/automating-your-broadcast-best-ai-tools-live-streamers-2026 — Streams Charts - panorama des outils IA de diffusion 2026
[14] https://presenc.ai/research/best-ai-tools-for-twitch-streamers-2026 — Presenc - panorama 2026 des outils IA pour streamers
[15] https://gamedayroundup.com/streamer-burnout-2026 — Game Day Roundup 2026 - burnout des streamers et charge mentale
[16] https://www.reddit.com/r/streamerbot/comments/1kkn4rc/streamerbot_wishlist — r/streamerbot - fil StreamerBot Wishlist (demandes d automatisation)
[17] https://murf.ai/blog/twitch-text-to-speech — Murf - guide TTS Twitch 2026 (usage reel du TTS par les viewers)
