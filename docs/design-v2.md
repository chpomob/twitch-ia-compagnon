# Design v2 — Compagnon agentique multi-plateforme

Date : 9 septembre 2026. Statut : **blueprint cible, à implémenter**.
Point de départ : `66d9a7e`, suite de référence de 189 tests.

Ce document fixe la direction produit et les contrats d'architecture. Il ne livre aucune fonctionnalité. **Existant** désigne le code à ce SHA ; **cible** désigne les décisions à réaliser en phases 0–2 ; **plus tard** désigne les extensions conditionnelles de phase 3+. Les nouveaux noms de contrats, chemins et paramètres ci-dessous sont des propositions normatives pour l'implémentation, pas des API déjà disponibles.

Autorité : les décisions utilisateur de septembre 2026 priment sur les questions encore ouvertes de la review du 08-09-2026 (`/tmp/twitchia-arch-review.md`, sections A, B1–B8 et C.1–C.5). En particulier, multi-plateforme, boucle agentique, déploiement local/distant, capture déléguée aux modules et déclenchements configurables par input sont **actés**. La review reste une source de travail externe, non nécessaire pour comprendre ce fichier. Le [plan](../plan.md), la [spec MVP](../spec.md) et l'[index documentaire](README.md) conservent leur rôle historique ; leurs garanties ne deviennent pas automatiquement celles de v2.

## 1. Vision et principes

Le compagnon est ultra-adaptable : plateformes, canaux, sources de contexte, modèle, sorties, permissions, limites et topologie sont configurables. Twitch est un module de plateforme parmi d'autres. Le nom du dépôt ne limite pas le produit à Twitch ni au texte.

Le **brain est un moteur de runs multi-turn** : le modèle peut demander une lecture du chat, observer une capture, puis décider de répondre. Les modules d'input/output sont des fournisseurs de **tools**, nommés **actions** dans le contrat runtime, y compris pour les lectures. Le modèle propose une action structurée ; un exécuteur contrôle et appelle son provider ; une observation texte et/ou image revient au modèle. Cette sémantique ne nécessite pas de dépendance à OpenAgents ni à un framework de skills.

Les responsabilités sont stables :

- Le **socle** possède les contrats, le registre, les services partagés, le moteur d'évaluation des triggers par input et la coordination du lifecycle.
- Le **brain** possède l'admission, l'ordonnancement par session, les tâches de run, les budgets et la conversation de travail du modèle.
- Chaque **module** possède ses capacités, transports, ressources et handlers ; le brain ne connaît ni OBS, ni la capture OS/jeu, ni Helix.
- Le **bus** transporte les faits, entrées normalisées et traces internes. Il conserve son contrat awaitable à chaîne ordonnée ; il n'est ni un RPC ni une preuve de livraison externe.
- La **configuration** sélectionne les modules et les bindings de providers, locaux ou proxies distants, derrière le même contrat d'action.

Le compagnon peut tourner sur le PC du streamer, sur un serveur, ou avec un brain serveur et des providers sur le PC. La cible comprend ces topologies ; la distribution du bus, une base de données et plusieurs instances concurrentes du brain ne sont pas des prérequis.

```text
Sources de plateformes → channel.chat.message → bus de faits
                                                  │ handlers courts
                                    contexte chat + trigger par input
                                                  │
                                          admission bornée
                                                  │
                                    files de sessions → workers limités
                                                  │
                              brain : modèle ⇄ exécuteur ⇄ observation
                                                  │
                                       registre de providers prêts
                                           /               \
                                  handler local      proxy → agent PC
                                                  │
                                  sortie acquittée → fin du run

Bus ← faits de santé, de trigger, d'admission, de run et d'action corrélés
```

## 2. Vocabulaire neutre plateforme

### 2.1. Événement de chat et destination

La cible conserve l'enveloppe de `core/bus.py` : `type`, `payload`, `metadata`, et le type existant `channel.chat.message`. `metadata.schema_version = 2` distingue le nouveau payload. Le bus reste générique ; les contrats de domaine valident les données à leurs frontières.

| Champ cible | Sens et contrainte |
| --- | --- |
| `payload.platform` | Identifiant stable de plateforme, par exemple `twitch` ; jamais déduit du nom affiché d'un module. |
| `payload.channel_id` | Identifiant opaque du canal dans cette plateforme. |
| `payload.author` | `id`, `display_name`, éventuellement `roles` et leur provenance fiable. L'affichage n'est pas une clé d'identité. |
| `payload.message_id`, `payload.text` | Identifiant source et texte borné du message. L'unicité du message est portée par `(platform, channel_id, message_id)`. |
| `payload.thread_id`, `payload.parent_message_id` | Optionnels ; absence explicite si la source ne les fournit pas. Aucun thread inventé. |
| `metadata` | `schema_version`, `event_id`, `source`, `provider_id`, `occurred_at`, `received_at`, puis corrélation disponible. Les timestamps sont UTC ; l'ordre local d'ingestion dispose d'une séquence par canal. |

`Destination` contient `platform`, `channel_id`, éventuellement `thread_id` et `parent_message_id`. L'identité d'envoi provient du binding configuré et des permissions du provider ; le modèle ne choisit pas librement un compte authentifié. Une destination hors périmètre est refusée.

La compatibilité d'entrée traduit les champs Twitch actuels `broadcaster_id`, `chatter_id`, `chatter_name`, `message_id`, `text` vers cette forme ; `metadata.source = twitch` reste conservé. La traduction se fait à la frontière, une seule fois, avec une plateforme explicitement connue. Les événements anciens ne sont pas réécrits dans les archives.

### 2.2. Identité, session et corrélation

**Décision actée — session hybride :** `SessionKey = (platform, channel_id, viewer_id)` pour les runs et la mémoire conversationnelle ; `viewer_id` provient de l'identité fiable de l'auteur (`author.id` pour le chat). Une éventuelle isolation par instance de compagnon entoure cette clé sans en changer la granularité. Le contexte global du chat utilise séparément `(platform, channel_id)`. Deux plateformes ou canaux ne partagent jamais de mémoire conversationnelle, même avec le même `viewer_id` ; les viewers d'un canal partagent uniquement son contexte de chat. Le thread reste une information de destination, pas une variante de session. La sérialisation doit être sans collision, sans concaténation ambiguë.

- `conversation_id` identifie cette session et sa mémoire ; sa clé reste stable pendant son existence.
- `run_id` identifie un travail admis jusqu'à sa terminaison, y compris attente en file et livraison finale.
- `call_id` identifie une invocation d'action. Il est généré par le runtime ; un identifiant de tool call du backend modèle est conservé séparément si nécessaire.
- `source_event_id` et `source_message_id` relient le déclencheur ; `provider_id` identifie le binding exécutant. Les identités de session et les droits viennent du runtime, pas des arguments inventés par le modèle.

Une identité absente ne doit jamais faire converger des viewers inconnus dans une même mémoire : rejeter l'admission si le module ne fournit pas d'identité viewer fiable. La destination et la session restent distinctes : une conversation n'autorise pas toutes les sorties du canal.

### 2.3. Faits de lifecycle et traces

Nouveaux types cibles, selon la convention pointée actuelle :

| Événements | Émetteur et contenu utile |
| --- | --- |
| `module.ready`, `module.degraded`, `module.stopped` | Supervision : provider/module, capacités concernées, raison assainie, date. |
| `input.trigger.accepted`, `input.trigger.rejected` | Ingestion : événement source, input/canal, version de politique, décision et motif borné ; aucun `run_id` à ce stade. |
| `brain.admission.accepted`, `brain.admission.rejected` | Admission : message, session, `run_id` si accepté, motif et profondeur de file. |
| `brain.run.started`, `brain.run.completed` | Brain : corrélation, délais, compteurs, état terminal et résultat de livraison séparé. |
| `action.started`, `action.completed` | Exécuteur : action, provider, `call_id`, statut terminal, durée et sommaire borné. |
| `channel.chat.sent` | Provider : fait d'envoi confirmé, destination et identifiant externe. Jamais déclenché par une simple acceptation du bus. |

Ces événements **décrivent** les transitions ; aucun subscriber ne doit répondre pour débloquer un appel. L'état terminal est enregistré chez son propriétaire avant la publication de sa trace. Une erreur d'audit ne peut ni annuler un effet externe ni déclencher sa répétition. Une séquence locale et les IDs permettent de reconstruire la causalité sans supposer un ordre global des publications.

L'audit conserve des métadonnées sélectionnées et expurgées, des statuts, temps d'attente, coûts/tokens si connus, rejets, abandons de messages périmés (`stale_drop`), timeouts et pertes de traces. Il n'archive ni images, ni prompts complets, ni raisonnement privé du modèle par défaut. Les arguments et résultats ne sont journalisés que sous une forme autorisée et bornée. Les événements internes et les propres sorties du compagnon ne sont pas des déclencheurs de run.

### 2.4. Déclenchements configurables par input

**Décision actée :** le streamer choisit une politique de déclenchement **par input/canal**, dans sa configuration ou ses profils. Les types disponibles dépendent du module d'input : ce n'est ni une politique globale ni une décision du brain. En l'absence de règle configurée par le streamer, chaque module déclare un **défaut propre à son type d'input**, configurable et remplaçable : par exemple, chat déclenché par un message contenant le nom du bot, vocal par un mot de réveil. Les règles explicites remplacent ce défaut ; toute combinaison doit être déclarée explicitement. Le socle évalue la politique effective à l'ingestion, avant l'admission, les budgets de run et tout appel modèle.

| Contrat cible | Déclaration et responsabilité |
| --- | --- |
| `TriggerSpec` | Déclaration versionnée des types supportés et des schémas de paramètres par le module d'input, dans son manifest (section 4.1), par exemple `triggers: [probability, audience, keyword]`. Le module fournit les prédicats, les faits spécifiques à sa source et son défaut par type avec ses paramètres. |
| Politique par input/canal | Règles sélectionnées et paramétrées par le streamer parmi ces capacités, sinon défaut déclaré par le module ; version de politique identifiée à l'ingestion. Types, paramètres et combinaisons non supportés sont refusés à la validation. |
| Évaluation | Fonction bornée, déterministe à événement normalisé, contexte fiable, configuration et dépendances injectées identiques. RNG et horloge sont injectables, notamment pour le probabiliste ; le tirage et les éléments nécessaires à la reproduction sont conservés sous une forme bornée et expurgée avec la décision. |

Le trigger transforme **événement ingéré → travail admissible pour un run brain**. Une acceptation ne crée pas encore de run et ne garantit pas son admission. Un rejet par trigger est une décision traitée et tracée, pas une erreur ; il ne lance aucun modèle. La déduplication conserve cette décision dans sa fenêtre bornée : une redélivrance du même événement n'effectue pas un nouveau tirage. La traçabilité suit les limites et garanties d'audit de la section 2.3.

Ordre à l'ingestion : **normalisation → alimentation séparée du contexte de canal → trigger effectif (règles explicites ou défaut du type) → admission bornée → run**. L'authenticité, la validation et les exclusions anti-boucle (propres sorties et événements internes) sont des invariants système appliqués à la frontière avant le trigger ; les contrôles de sécurité restent également actifs à l'exécution. Le streamer ne peut pas désactiver ces invariants par une règle de trigger. L'admission de charge reste toujours active pour les événements acceptés : aucune politique, même « toujours », ne contourne la saturation. Le trigger précède les budgets de run, sans supprimer les bornes des transports, de l'ingestion ou de sa propre évaluation.

| Input | Exemples de règles, syntaxe illustrative |
| --- | --- |
| Chat | `probability: 0.1` : 10 % de chance par événement unique ; `audience: subscribers` : seuls les messages d'abonnés ; `keyword: [bonjour, compagnon]` : mots-clés configurés. |
| Vocal | Types propres au module vocal, par exemple `wake_word` ou `voice_command` : le module détecte un mot de réveil ou une commande vocale et fournit l'événement normalisé nécessaire à l'évaluation. Ces capacités ne sont pas imposées à tous les inputs. |

Les combinaisons sont possibles selon les capacités déclarées du module, par exemple abonnés **ET** mot-clé **ET** probabilité de 10 %. Les opérateurs et leur ordre d'évaluation doivent être explicites et testables ; aucune composition implicite. Les prédicats de rôles/abonnement utilisent un contexte fiable fourni par la plateforme ou le provider, avec provenance ; le texte d'un message ou d'une transcription ne prouve jamais un rôle. Une information absente ne satisfait pas un prédicat exigeant ce rôle.

Une observation retournée à un run par `chat.read` ou `audio.capture` ne déclenche pas un autre run. Les triggers vocaux concernent les entrées spontanées émises par le module vocal lorsqu'il supporte cette capacité ; ils ne promettent pas une écoute continue dès la première capture audio. Le principe du défaut par type est acté ; la syntaxe exacte des déclarations, paramètres et compositions reste un contrat d'implémentation à préciser en phase 0 pour le chat, puis avec chaque nouvel input.

**Existant :** aucun moteur de triggers configurables n'est implémenté ; `BrainModule.handle_chat_message` appelle aujourd'hui le modèle pour tout message valide reçu, hors arrêt. Ce comportement n'est pas un défaut produit adopté pour v2 ; la phase 0 introduit la sélection par input.

## 3. Brain agentique : moteur de runs

### 3.1. Boucle et propriété des tâches

Un run peut terminer directement depuis `queued` en cas d'expiration (`timeout`, motif `stale_drop`) ou d'annulation ; sinon, il passe de `queued` à `running`, puis `finalizing` si une réponse doit être livrée, et enfin à un état terminal : `success`, `refused`, `error`, `timeout`, `cancelled` ou `external_unknown`. `success` exige la confirmation de toutes les sorties requises ; une fin sans sortie autorisée par le produit indique explicitement `delivery = not_requested`. Des appels intermédiaires peuvent échouer et être récupérés sans faire échouer tout le run. Tout effet restant incertain est signalé dans le bilan ; il interdit un bilan global de succès certain.

À chaque étape, le brain :

1. Construit un contexte borné et la liste des actions prêtes et autorisées pour cette session.
2. Appelle l'adaptateur modèle, qui retourne soit une proposition `ActionProposal(name, arguments)`, soit `FinalResponse`.
3. Pour une action, crée `ActionCall`, attend l'exécuteur, ajoute l'observation structurée au transcript, puis rappelle le modèle si les budgets le permettent.
4. Pour une réponse finale, sélectionne la sortie configurée et appelle le même exécuteur avec le tool d'envoi correspondant. Cette livraison compte dans les budgets ; elle n'est pas un nouvel appel modèle obligatoire.
5. Enregistre le bilan, met à jour la mémoire admissible et libère les ressources du run.

**Adaptateur modèle acté :** un seul adaptateur « OpenAI-compatible », limité aux endpoints classiques au format Chat Completions de type OpenAI ; endpoint, modèle et clé restent configurables comme aujourd'hui. La compatibilité visée couvre les backends OpenAI, Ollama et les serveurs locaux exposant ce format, sous réserve des capacités vérifiées. Aucun adaptateur propriétaire multiple en v1. L'adaptateur détecte/vérifie la sortie structurée nécessaire aux propositions d'actions, la vision en phase 1, puis l'audio lorsque requis en phase 2. Un backend dépourvu d'une capacité requise est refusé explicitement, sans suppression silencieuse d'une image, d'un audio ou d'une structure attendue. Le format compatible seul ne prouve pas ces capacités.

Une seule action est exécutée à la fois dans un run en phase 1. Une réponse backend contenant plusieurs appels est refusée comme forme non supportée, sans effet partiel. Les actions explicites d'écriture ne sont permises que par la politique ; elles sont suivies dans le bilan pour éviter une seconde livraison automatique du même contenu. La verticale initiale n'autorise aucune écriture intermédiaire : seule la réponse finale peut être envoyée, avec une règle d'autorisation explicite applicable à l'envoi.

Le brain conserve toutes ses tâches, y compris celles détachées du handler d'entrée, jusqu'à leur terminaison ou transfert explicite au superviseur d'arrêt. L'exécuteur conserve de même les appels en vol. Un résultat tardif ne rouvre jamais un run terminé.

### 3.2. Contrat d'action et d'appel

Contrats partagés cibles dans `core/contracts.py`, registre/exécution dans `core/actions.py` ; aucun de ces fichiers n'existe à la référence de départ.

| Contrat | Champs et règles |
| --- | --- |
| `ActionSpec` | `name` pointé stable, `version`, `description`, `arguments_schema`, `result_schema`, nature `read` ou `write`, permissions requises, destinations/ressources supportées, politique de timeout et d'idempotence. Schémas JSON Schema avec dialecte déclaré et version de contrat vérifiée. |
| `ActionCall` | Version, `name`, version d'action, arguments validés, `conversation_id`, `run_id`, `call_id`, événement/message déclencheur, destination, principal fiable, deadline, éventuelle clé d'idempotence. |
| `ActionProvider` | Interface async `invoke(call) -> ActionObservation`, identique pour local et proxy ; le handler enregistré appartient à un seul module/provider. |
| `ActionExecutor` | Valide forme et bornes, résout un provider unique pour l'action/destination, vérifie disponibilité et permissions avec refus par défaut, y compris pour les lectures, applique délais et conflits de ressources, valide le résultat. |

Un binding doit désigner **un seul exécuteur** pour une action et son périmètre de destination. Deux bindings ambigus font échouer la préparation ; aucune diffusion à plusieurs sinks ni fallback automatique après un effet incertain. Plusieurs providers peuvent proposer le même tool pour des destinations disjointes ou avec une sélection explicite par configuration.

Le modèle propose ; **l'exécuteur refuse par défaut : en l'absence de règle d'autorisation applicable, toute action est refusée, y compris une lecture.** Il n'existe aucune action auto-autorisée. Le contrôle est répété à chaque appel, même si l'action figurait dans le prompt.

**Les règles d'autorisation sont posées par le streamer (configuration/grants) et/ou déclarées par les modules d'input/output dans leur manifest/module, sous forme de règles déclaratives : chaque module connaît ses exigences.** Déclarer une action, sa nature `read`, ses permissions requises ou sa disponibilité ne constitue pas à soi seul une règle qui l'autorise. Le format des règles, leurs combinaisons et leur portée canal/session restent à définir plus tard (section 7) ; aucun ordre de priorité implicite n'est fixé ici. Une liste autorisée au début du run n'est pas une permission permanente. Le provider applique également ses contraintes externes et, à distance, sa propre autorisation. Les rôles absents ne sont pas présumés ; chat, OCR et observations sont des données externes, incapables d'élargir les droits.

### 3.3. Observation et résultats terminaux explicites

`ActionObservation` contient la version, la corrélation, `status`, des `parts`, les données structurées validées par `result_schema`, la provenance (`provider_id`, source, date d'observation/capture), et éventuellement une erreur normalisée (`code`, message assaini, indication de réessai). Même une action d'output retourne une observation.

| `status` terminal | Signification |
| --- | --- |
| `success` | Lecture obtenue ou effet confirmé selon le contrat du provider ; référence externe conservée si disponible. |
| `refused` | Absence de règle d'autorisation applicable, ou politique, destination, permission ou disponibilité qui interdit l'appel ; aucun effet engagé. |
| `error` | Arguments invalides, réponse malformée ou échec certain ; code métier précis. Une réponse perdue après émission d'un effet n'entre pas ici. |
| `timeout` | Délai dépassé sans ambiguïté d'effet externe, par exemple une lecture expirée. |
| `cancelled` | Annulation confirmée sans effet incertain. |
| `external_unknown` | L'effet a pu avoir lieu, mais sa confirmation manque ; inclut timeout/déconnexion/annulation après émission non réconciliée. |

Le champ `cause` peut distinguer timeout et annulation derrière `external_unknown`. L'absence de provider ou de résultat n'est jamais un succès. L'exécuteur normalise les exceptions attendues en observation ; les pannes d'infrastructure restent aussi visibles du superviseur. Une annulation du run conserve son contrôle asyncio : l'observation terminale est enregistrée pour le bilan, sans relancer le modèle annulé.

Les parties sont typées : `text` avec texte limité ; `image_ref` avec `attachment_id`, MIME, taille, dimensions, provenance et date de capture. La phase audio étend ce contrat avec `audio_ref`, MIME, durée et éventuellement transcription texte. Le résultat peut combiner plusieurs parties ; ni objets Python arbitraires, ni chemins locaux utilisables à distance, ni images base64 dans les événements.

`AttachmentStore` (cible : `core/attachments.py`) possède les octets. Il impose taille par objet, nombre d'objets, volume global et par run, TTL et accès limité aux destinataires autorisés. Les références actives sont louées au run jusqu'à sa deadline ; si l'espace manque, l'ajout est refusé, sans croissance illimitée ni éviction silencieuse d'une observation en usage. Fin/annulation libèrent les références ; une expiration résiduelle assure le nettoyage. Une référence expirée retourne une erreur explicite.

L'adaptateur modèle résout les références et encode les médias au format du backend seulement pendant l'appel. Un proxy transfère les pièces jointes vers le store autorisé ou fournit une résolution authentifiée bornée ; un chemin sur le PC n'est pas une observation exploitable par le serveur. Le provider de capture applique la sélection de fenêtre/zone et les permissions avant acquisition et transmission ; sans règle applicable, aucune capture n'a lieu. Toute observation, même une lecture du cache local, passe par le même refus par défaut.

### 3.4. Admission, sessions et budgets

Le handler `BrainModule.handle_chat_message` cible valide et copie une entrée bornée, tandis que l'ingestion alimente séparément le contexte par plateforme/canal, puis fait évaluer le trigger effectif par input (section 2.4). Seul un événement accepté par ce trigger passe à l'admission immédiate ; le handler n'attend pas le modèle. La copie est indépendante des mutations ultérieures du bus. La lecture réseau ne crée pas une tâche illimitée par message : toute file intermédiaire de source est également bornée et possède une politique de saturation.

Un ordonnanceur possède une file FIFO bornée par `SessionKey`, un plafond global de travaux en attente, un plafond de sessions et un nombre limité de workers. Chaque session `(platform, channel_id, viewer_id)` a au plus un run actif ; les sessions distinctes peuvent progresser en parallèle, avec répartition équitable. Les files/session locks inactifs sont évincés. Les actions partageant une ressource de canal, comme un sondage ou une scène, ajoutent un verrou ou une file **bornée** chez le provider : sérialiser les conversations ne suffit pas.

**Décision actée :** tout nouveau message accepté pendant un run rejoint la FIFO bornée de sa session, sans fusion ni interruption du run actif. Chaque message en file possède une deadline d'attente finie, configurable par profil et au plus égale à la deadline totale du run calculée à l'admission. Si elle expire, le travail est abandonné avant tout appel modèle : état terminal `timeout`, motif `stale_drop`, trace `brain.run.completed` et compteur avec durée d'attente ; aucun démarrage ni réponse obsolète pour ce message. L'ordonnanceur contrôle cette échéance en file et avant démarrage. File pleine ou plafond global atteint : rejet du nouveau travail, tracé par `brain.admission.rejected` avec motif et compteur, sans appel modèle ni replay promis. Le contexte de chat conserve le message ingéré dans ses bornes propres, indépendamment de l'admission, d'un rejet trigger ou d'un abandon périmé. Le choix d'un éventuel repli générique lors d'une expiration reste ouvert en section 7.

L'acceptation par l'admission signifie seulement que le runtime possède un travail en mémoire. La déduplication source et le contexte enregistrent une décision d'ingestion unique avant tout travail long ; un rejet explicite par trigger ou pour surcharge est une décision traitée, pas une exécution réussie. Une erreur technique avant cette décision peut être retentée. Les callbacks courts d'ingestion doivent être idempotents sur la clé du message, même si un subscriber ultérieur échoue. Une panne processus peut perdre un run accepté : aucune garantie durable avant phase 3+.

Chaque profil configure des valeurs finies et validées pour : attente maximale, deadline totale **depuis admission jusqu'à livraison**, tours modèle, appels d'actions (livraison incluse), tokens entrée/sortie/cumul, volume d'observations et médias, délai par modèle/action, débit et budget par canal. Les défauts actés ci-dessous peuvent tous être remplacés par profil ; les autres limites doivent également être définies et validées, sans valeur numérique inventée ici. Aucun profil agentique n'est utilisable avec des limites omises ou infinies. Le runtime réserve de la marge pour la sortie et borne le prochain appel par le temps restant. Si le backend ne fournit pas les tokens réels, une estimation conservatrice est signalée comme telle et les tailles d'entrée/sortie restent plafonnées.

| Budget | Valeur par défaut actée |
| --- | --- |
| Tours modèle par run | 5 maximum |
| Délai par appel modèle | 30 s |
| Délai par action, livraison incluse | 10 s |
| Deadline totale admission → livraison, attente en file incluse | 60 s |
| Tokens cumulés entrée + sortie par run | ≈ 8K |
| Taille maximale d'une observation ou d'un média | ≈ 5 Mo |

Les valeurs approximatives deviennent des plafonds numériques explicites dans les profils d'implémentation. Les vérifications de capacités de l'adaptateur unique (section 3.1) précèdent l'utilisation du backend ; les appels modèle et les encodages médias restent soumis aux budgets. L'acceptation d'un trigger ou d'un run n'accorde aucun droit d'action : lectures, captures et livraison exigent chacune une règle applicable. Dépasser une borne arrête le travail concerné avec un bilan explicite ; **silence ou message de repli reste une décision ouverte**, sans réponse métier obsolète ni renouvellement de deadline. Une éventuelle sortie de repli exige elle aussi autorisation et budget de sortie disponible ; aucun envoi après la deadline totale.

La deadline locale utilise une horloge monotone. Un proxy transmet une échéance UTC et un temps restant ; le receveur applique une limite conservatrice et ne renouvelle jamais le budget à chaque saut. Le protocole doit tester les écarts d'horloge et la latence ; des timestamps muraux seuls ne suffisent pas.

L'anti-boucle est un invariant système que les triggers configurables ne peuvent pas désactiver ; elle combine filtre anti-écho existant, exclusion des traces comme déclencheurs, quotas par canal, limite de tours et détection de répétitions action/arguments/observation sans progrès. Les lectures répétées peuvent être utiles si leur timestamp change, mais restent budgétées. Après un effet incertain, aucun retry aveugle : utiliser une idempotence effectivement supportée ou réconcilier l'état. `call_id` permet la corrélation, sans garantir à lui seul une exécution exactement une fois.

### 3.5. Trois mémoires séparées, toutes bornées

| État | Propriétaire et politique cible |
| --- | --- |
| Transcript de run | Brain ; appels, observations et sortie provisoire, bornés en tokens/octets et supprimés à la fin sauf résumé autorisé. Aucun stockage du raisonnement privé. |
| Historique conversationnel | Brain ; évolution de `_histories`, indexée par `(platform, channel_id, viewer_id)`, bornée en sessions, échanges complets, tokens/octets et TTL. N'affirme livrées que les sorties acquittées ; les incertitudes restent explicitement marquées. |
| Contexte du chat | Module de lecture ; alimenté rapidement à l'ingestion, indépendamment des triggers, admissions et runs, même sans réponse. Fenêtre par plateforme/canal, limites en messages/octets/âge, lectures datées et paginées. |

`EventBus.list_events()` demeure un outil d'inspection borné, pas une mémoire conversationnelle : l'historique actuel est enregistré en ordre de fin et n'inclut pas nécessairement l'entrée en cours. Les snapshots du contexte sont ordonnés à l'ingestion ; le message déclencheur est disponible avant que son run démarre.

## 4. Modules : déclaration, exécution et topologie

### 4.1. Manifest versionné et activation

La cible étend `modules/*/module.yaml` avec `manifest_version: 2`, `runtime_api: 2`, `settings_schema`, `actions`, `triggers` pour les modules d'input et des dépendances/capacités de lifecycle. Les déclarations `triggers` décrivent les types, versions, schémas de paramètres et combinaisons supportés ainsi que le défaut propre au type d'input (`TriggerSpec`, section 2.4) ; les règles explicites de la config/profil de chaque input/canal remplacent ce défaut et restent distinctes des actions exposées au modèle. Les modules d'input/output peuvent également déclarer leurs règles d'autorisation dans le manifest/module, séparément des triggers et des `ActionSpec` ; le format et la composition avec les grants du streamer restent à définir. Aucun grant implicite n'est déduit d'une déclaration de capacité. Chaque entrée `actions` porte les champs de `ActionSpec` ; un schéma peut être inline ou référencé dans le répertoire du module, avec validation des chemins. `produces`, `consumes`, `middleware`, `order` conservent leur sens événementiel. Déclarer `produces: [channel.chat.send]` n'autorise pas l'action `chat.write`.

Le loader distingue trois vues :

1. **Découvert** : manifest valide, même désactivé ; catalogue immuable pour introspection, jamais présenté comme liste d'outils utilisables.
2. **Enregistré et prêt** : handler async enregistré pendant l'activation/préparation, schémas compatibles, ressources et authentification utilisables. Un enregistrement seul ne signifie pas prêt.
3. **Autorisé** : sous-ensemble prêt filtré par principal, session, destination et règle d'autorisation applicable ; sans règle, l'action est exclue, même en lecture. Seule cette vue est exposée au modèle ; contrôle répété à l'exécution.

Un `RuntimeContext` versionné (cible : `core/runtime.py`) injecte bus, registre/exécuteur, store, moteur de triggers, RNG et horloge injectables, supervision, diagnostics et factories de transports. La configuration YAML ne contient que des données ; les callables de tests quittent progressivement les settings. L'API v2 cible `validate_settings(settings)` puis `activate(context, settings, catalog)`, qui retourne un handle préparé et enregistre ses handlers async via `context.actions.register(spec, handler, provider_id)`. L'activation ne démarre pas les producteurs.

Le core valide YAML, environnement, chemins et versions ; chaque module valide ses contraintes métier, pour **tous les activés avant leurs effets réseau**. Les imports doivent rester sans effets externes. Les manifests désactivés restent validés ; leurs secrets ne sont pas résolus ni leurs settings métier exigés. `${NAME}` reste une référence entière ; conversion et bornes des nombres appartiennent au schéma/hook du module. Un modèle local ne requiert une clé que si le backend OpenAI-compatible configuré l'exige.

### 4.2. Lifecycle par phases

Le handle v2 expose des hooks async idempotents et bornés ; un module sans rôle dans une phase fournit une opération vide. `core/main.py` coordonne ces phases sans tri par noms métier.

| Phase | Garantie |
| --- | --- |
| Validation, puis préparation par `activate` | Tous les settings sont validés ; enregistrer consommateurs, handlers, ressources et tâches supervisées sans émettre d'entrées. Nettoyer localement si le handle n'a pas encore été rendu. |
| Barrière de préparation puis `start_inputs()` | Tous les consommateurs et providers requis sont prêts avant la première entrée ; les politiques de triggers (défaut compris) et les règles d'autorisation déclarées sont validées, le refus par défaut est actif et l'évaluation des triggers ainsi que l'admission sont opérationnelles avant ouverture des producteurs. Readiness globale après démarrage réussi. |
| `stop_inputs()` | À l'arrêt, couper les nouvelles sources et terminer les admissions courtes en vol ; fermer ensuite l'admission. Conserver les transports nécessaires aux outputs et lectures des runs. |
| `drain(deadline)` | Terminer les travaux acceptés dans le budget d'arrêt ou les annuler explicitement ; garder les exécuteurs et sorties disponibles jusqu'aux bilans terminaux. |
| `close()` des ressources ordinaires | Fermer transports et stores après les appels ; collecter les erreurs sans ignorer les autres modules. |
| `flush(deadline)` puis fermeture des services d'observation | Finir les traces après les dernières fermetures, puis fermer l'audit. Ce rôle est déclaré, pas associé au nom `audit`. |

Une deadline globale couvre startup et une autre shutdown ; les délais locaux ne s'additionnent pas sans plafond. Le superviseur observe les erreurs des tâches, dégrade les providers et retire leurs capacités prêtes. Une panne d'un provider obligatoire rend la santé globale dégradée ou arrête selon politique explicite.

Annuler une coroutine ne prouve pas l'arrêt d'une capture native ou d'un writer en thread. Le module doit choisir une exécution bornée ou isolable et signaler les tâches résistantes ; le CLI conserve un ultime watchdog processus avec sortie non nulle et pertes possibles signalées. L'usage embarqué expose l'échec de fermeture au propriétaire du processus ; il ne peut garantir de tuer un thread arbitraire. Démarrage partiel et annulation suivent les mêmes responsabilités de nettoyage.

### 4.3. Provider local ou proxy distant

La configuration cible ajoute des bindings explicites : nom/version de tool, `provider_id`, destinations permises, `transport = local|remote`, référence du module local ou endpoint authentifié distant, contraintes et budgets. Ce vocabulaire est un **schéma cible**, pas des clés acceptées par `config.yaml.example` aujourd'hui. Brain appelle toujours `ActionExecutor`, jamais une URL ou une API de capture.

| Topologie cible | Répartition |
| --- | --- |
| Tout sur le PC | Brain, modules et stores locaux ; les services de plateforme/modèle peuvent être externes. |
| Serveur autonome | Brain et modules réseau sur serveur ; une capture nécessite une source accessible depuis ce serveur. |
| Serveur + agent PC | Brain serveur ; proxy de capture/audio/scène vers un agent PC qui détient permissions, périphériques et transports locaux. |

Le protocole proxy versionné transporte `ActionCall`/`ActionObservation`, annulation, contrôle de santé et résolution des pièces jointes. Il impose authentification du pair, chiffrement réseau, contrôle local des destinations/ressources avec refus par défaut, même pour une lecture ; l'authentification du pair ne vaut pas grant d'action. Il impose aussi des limites de payload et de travaux, des deadlines et une déduplication bornée. Un même appel retransmis ne doit pas lancer une seconde exécution tant que son entrée d'idempotence est conservée ; après expiration ou crash, il peut rester incertain. Une reconnexion n'autorise pas le replay automatique des écritures.

Les entrées spontanées distantes utilisent un adaptateur de source qui authentifie et normalise les événements avant publication dans le bus local. Cela ne distribue pas `EventBus` et n'utilise pas ses abonnements pour corréler les retours d'actions. La proposition encore ouverte est WebSocket JSON avec token, un agent PC par brain, pour un brain sur serveur ou PC ; elle ne remplace pas les exigences du protocole ci-dessus. Le transport réseau concret est choisi et testé en phase 1 ; l'interface commune et le support du mode distant sont des exigences immédiates.

### 4.4. Les sept modules starter

Les sept fonctions suivantes constituent le kit cible, livré progressivement en phases 1–2. Ce sont des unités configurables ; elles peuvent partager un provider de plateforme et ses connexions. Les noms de dossiers proposés n'imposent pas sept duplications de credentials ni sept connexions Twitch. **Aujourd'hui, seuls `twitch`, `brain`, `audit` existent.** `modules/twitch/` reste l'adaptateur Twitch partagé ; les nouveaux tools ci-dessous ne sont pas déjà enregistrés.

| Module starter cible | Tools, sens et résultat | Dépendances externes et local/distant |
| --- | --- | --- |
| Lecture chat — `modules/chat_context/` | `chat.read` : input/observation, destination + fenêtre/limite → messages normalisés, curseur, date et couverture. | Cache alimenté par les sources ; Twitch via EventSub dans `modules/twitch/`. Pas de requête plateforme nécessaire pour lire le cache. Provider près de l'ingestion ou proxy ; résultat indique une fenêtre partielle/expirée. |
| Lecture users — `modules/users/` | `users.read` : input/observation, destination + pagination/filtre borné → identités, rôles fiables disponibles, date et couverture. | Provider Twitch via opérations Helix adaptées et enrichissements EventSub disponibles. Autorisations et disponibilité vérifiées à l'implémentation ; ne promet pas une liste exhaustive des spectateurs. Local PC ou serveur/proxy derrière le même contrat. |
| Écriture chat — `modules/chat_output/` | `chat.write` : output/action, `Destination`, texte, parent optionnel → confirmation, identifiant externe et destination. | Délègue à l'envoi Helix de `modules/twitch/` pour Twitch ; autres adaptateurs pour autres plateformes. PC, serveur ou proxy ; quotas et droits restent au provider. |
| Screenshots stream/jeu — `modules/capture/` | `screen.capture` : input/observation, source/zone autorisée → `image_ref`, MIME, dimensions, provenance et timestamp. | OBS, capture OS, fenêtre ou API jeu selon provider. Acquisition sur la machine ayant accès à la source ; proxy si brain distant. Source, zone et cadence configurées dans le module ; observation ponctuelle demandée par le brain, sans capture de fond. |
| Output audio — `modules/audio_output/` | `audio.speak` : output/action, texte + voix/sortie autorisées → état de lecture confirmé ; `audio.play` : référence audio → état de lecture. | TTS local ou service distant, périphérique audio/OBS. Le backend TTS peut être serveur ; la lecture finale est sur la machine de sortie, éventuellement via proxy. Le succès signifie lecture terminée selon contrat, pas seulement synthèse créée. |
| Capture audio — `modules/audio_input/` | `audio.capture` : input/observation, source + durée bornée → `audio_ref` et transcription optionnelle datée. | Micro, mix système ou OBS ; transcription locale ou distante si configurée. Acquisition près du périphérique, transport borné des segments si brain serveur. Capture à la demande du brain ; sources/sorties et durée relèvent de la configuration des modules. Les entrées vocales spontanées de la section 2.4 sont une capacité distincte, sans capture de fond implicite. |
| Interaction stream — `modules/stream_control/` | `stream.scene.set` : output/action, scène autorisée → état OBS confirmé ; `stream.poll.create` : output/action, question/options/durée bornées → identifiant et état du sondage. | Scènes via provider OBS sur PC ou proxy ; sondages Twitch via Helix, suivi d'état EventSub selon capacités disponibles. Permissions distinctes et exclusion mutuelle par ressource. La modération ultérieure exige ses propres tools/permissions, sans autorisation implicite. |

Source de capture (OBS/OS/jeu), zone, cadence et sources/sorties audio relèvent de chaque module et de sa configuration : ni décision produit globale, ni gate de phase pour le core. La cadence naturelle est **action-driven** : le brain demande une observation quand il en a besoin, le module réalise la capture, sans capture de fond. Tous les tools du tableau, lectures comprises, exigent une règle d'autorisation applicable et respectent les budgets.

Les détails d'endpoints, scopes, formats vision/audio et plateformes supportées doivent être vérifiés lors de chaque implémentation ; ce tableau fixe les responsabilités et résultats attendus. Un module sans sa dépendance externe reste découvert mais non prêt. Les fonctionnalités disponibles doivent être visibles dans l'état runtime, pas seulement dans le prompt.

## 5. Migration depuis l'existant

### 5.1. Blocages de la review et réponse cible

| Blocage | Existant vérifié | Réponse cible et emplacement |
| --- | --- | --- |
| B1 — Admission | `_consume` attend la publication ; `_publish_lock` de Twitch couvre le modèle et la sortie. | Admission et FIFO bornées par session avec deadline et `stale_drop`, workers brain ; `modules/twitch/__init__.py` et `modules/brain/__init__.py`, phase 0. |
| B2 — Lifecycle | `_close_activations` trie Twitch d'abord, audit dernier ; Twitch possède les publications en cours. | Phases génériques et propriété des runs ; `core/main.py`, `core/loader.py`, handles des trois modules, dans le même lot que B1. |
| B3 — Actions ≠ événements | Tags `[send:…]`, mutation `delivery_status`, pas d'exécuteur unique ni de retour au modèle. | `ActionExecutor`/registre et acquittement terminal dès phase 0 ; boucle et observations phase 1. `channel.chat.send` devient une route de compatibilité. |
| B4 — Capacités | Catalogue de tous les manifests, désactivés inclus, utilisé dans le prompt. | Séparer découvert, prêt et autorisé ; manifests + registre/runtime ; exposition filtrée et refus par défaut à l'exécution, lectures comprises. |
| B5 — Sessions | `_histories` indexé par viewer ; verrou de copie, pas de sérialisation d'un échange. | Sessions plateforme/canal/viewer, contexte global séparé par plateforme/canal alimenté à l'ingestion, files par session et workers globaux en phase 0 ; transcript distinct en phase 1. |
| B6 — Rétention | Historique du bus, queue audit et dédup Twitch non bornés ; mémoire viewer bornée seulement en nombre. | Limites en nombre/volume/âge, politique de saturation, dédup à fenêtre explicite et store média borné. |
| B7 — Validation | `_MODULE_REQUIRED_FIELDS` de `core/main.py` duplique `_Settings.from_mapping` ; références des désactivés résolues. | Hooks et schémas de modules avant effets réseau, contexte runtime séparé, secrets des seuls activés. |
| B8 — Observabilité | Audit écrit `type`/`payload`, omet `metadata` et peut perdre silencieusement des records. | Traces corrélées, santé et compteurs de pertes bornés ; socle phase 0, instrumentation de boucle complète phase 1. |

### 5.2. Ce qui est gardé, ce qui évolue explicitement

**Gardé :** `EventBus.subscribe/publish`, motifs `*`/`**`, ordre stable et erreurs de publication ; découverte de répertoires/manifests par `ModuleLoader` ; transports HTTP/WebSocket/writer injectables ; vrais modules derrière de faux transports en intégration ; filtre d'auto-écho Twitch, reconnexion et diagnostics expurgés. Les neuf fichiers `tests/test_{bus,loader,main,twitch,brain,audit,examples,integration,shutdown}.py` constituent la base de non-régression, pas une preuve de v2.

**Transition API :** absence de version de manifest signifie v1. Un chemin explicite conserve `activate(bus, settings, catalog)` et `close()` pour les modules v1. Il ne prétend pas extraire magiquement leurs phases : un producteur v1 incompatible avec la barrière doit être migré ou refusé dans le profil agentique. Les trois modules intégrés migrent ensemble ; aucun attribut de contexte caché n'est ajouté au bus.

**Transition sorties :** les tags restent confinés à l'adaptateur modèle historique. La route `channel.chat.send` et le tool `chat.write` convergent vers un service d'envoi commun, avec un seul chemin choisi par invocation. Le brain v2 utilise directement l'exécuteur et ne republie pas la commande en parallèle. Les clients historiques peuvent encore lire `delivery_status = sent|failed`, mais les nouvelles actions disposent du résultat complet, dont `external_unknown`. L'absence de statut n'est plus interprétée comme une livraison v2. Le parent de réponse est transmis explicitement à partir de `Destination`.

**Transition autorisations et modèle :** aucun droit implicite de l'existant n'est repris en v2. Les tests et profils de phase 1 devront configurer explicitement les grants nécessaires, y compris `chat.read`, `users.read`, `screen.capture` et `chat.write` lorsqu'ils sont utilisés ; la compatibilité des sorties ne contourne pas l'exécuteur. Endpoint, modèle et clé restent configurables derrière l'unique adaptateur OpenAI-compatible ; la sortie structurée et les capacités médias doivent être vérifiées, sans multiplication d'adaptateurs propriétaires.

**Rétention cible décidée :** historique bus circulaire limité en événements/octets/TTL ; file audit limitée en records/octets, admission non bloquante et suppression du nouveau record en surcharge, avec compteur de pertes et diagnostic agrégé hors de la file saturée ; dédup source limitée par taille et TTL. La garantie de dédup v2 couvre **la fenêtre conservée**, pas toute l'activation. Une répétition après éviction peut être retraitée. Une garantie plus longue impose un stockage adapté et n'est pas promise par le socle mémoire.

Les bornes ci-dessus sont actées ; la rétention après stream/redémarrage et l'accès aux données restent ouverts. Proposition : tout volatile, TTL courts et audit fichier optionnel rotatif (section 7).

Ces choix changent des garanties historiques : R1 pour l'inspection de l'historique, R4/AC10 pour la déduplication et R6/AC17 pour l'exhaustivité audit sous surcharge. R2/R3 évoluent avec les contrats d'activation/configuration ; R5/R7 avec le modèle et les modules. **La phase 0 devra versionner les exigences et adapter les tests concernés dans ses propres changements. Ce commit documentaire ne modifie ni spec, ni plan, ni tests.** La compatibilité signifie une transition explicite et testée, pas la conservation silencieuse de promesses incompatibles avec des bornes finies.

Le packaging fait partie de la livraison : `pyproject.toml` n'inclut actuellement que `core*`. Une installation v2 devra fournir les modules starter, leurs manifests et des profils de configuration installables ; l'exécution depuis le checkout n'est pas une preuve de distribution fonctionnelle.

## 6. Roadmap phasée et critères de sortie

Les phases v2 ne renumérotent pas les étapes historiques P1–P9. Les critères ci-dessous sont à écrire et exécuter lors de l'implémentation ; les 189 tests actuels ne les couvrent pas tous. Les décisions produit bloquant une phase sont listées en section 7.

### Phase 0 — Socle cohérent avant multi-turn

**Lot indissociable : moteur de triggers par input + admission bornée + lifecycle par phases + registre/exécuteur d'actions + identités et ordonnancement de session + rétention bornée.** Ajouter validation de modules, supervision et traces minimales. Livrer `TriggerSpec`, déclarations des capacités et de leurs défauts par type, configuration par input/canal et refus d'action par défaut, puis évaluation avant admission ; appliquer les premières politiques au chat pour remplacer l'appel modèle systématique actuel avant la verticale agentique. Les types vocaux arrivent avec les capacités du module audio en phase 2. Garder initialement un seul appel modèle par travail ; faire passer sa sortie par le résultat explicite du service d'envoi. Définir les références médias et tester le quota du store avant d'intégrer une capture.

Critères de sortie :

- Deux inputs/canaux peuvent appliquer des politiques distinctes ; sans règle explicite, le défaut du type s'applique (chat : nom du bot), et une règle configurée le remplace ; types/combinaisons non déclarés refusés avant réseau. Probabilité, abonnement fiable et mot-clé sont vérifiés avec RNG/horloge injectés : même décision reproductible, aucun nouveau tirage sur doublon dans la fenêtre de dédup. Un rejet trigger est tracé sans admission ni appel modèle ; une acceptation reste soumise à la saturation et aux invariants anti-boucle/sécurité.
- Un faux modèle maintenu en attente n'empêche pas la réception d'un deuxième message ni d'un keepalive ; les files et tâches restent sous leurs limites en surcharge, avec rejets comptés. Une deadline d'attente expirée provoque un `stale_drop` tracé sans appel modèle ni réponse obsolète, vérifié avec horloge injectée.
- Deux messages d'une même session voient les historiques dans l'ordre ; une autre session progresse dans la limite des workers ; mêmes IDs de viewer sur deux plateformes/canaux ne partagent pas leur mémoire. Deux viewers d'un même canal ont des conversations distinctes et lisent, si autorisés, le même contexte global, alimenté même après rejet trigger/admission ou `stale_drop`.
- Une activation de consommateur volontairement lente ne laisse passer aucune entrée avant la barrière. Un quatrième module producteur/sink fictif démarre et s'arrête sans ajout de nom dans `core/main.py`.
- Après retour du handler d'admission, un arrêt conserve le transport de sortie jusqu'au bilan du run détaché ; délai épuisé : annulation et statut explicites. Couvrir chaque phase, startup partiel, `CancelledError`, tâche résistante et writer bloqué.
- Un doublon de binding, un schéma incompatible, un module désactivé, un provider non prêt ou une absence de sink ne produisent aucun faux succès. Sans règle applicable, lecture comme écriture retournent `refused` sans invocation du provider ; les scénarios autorisés configurent leurs grants. Les validations métier échouent avant réseau, sans secret dans les diagnostics.
- Dépassement des limites bus/audit/dédup/mémoires/store : bornes respectées et comportement d'éviction/rejet prouvé avec horloge injectée, y compris replay après expiration de dédup.
- `tests/test_shutdown.py` et les assertions d'intégration sont adaptés à la nouvelle propriété des tâches : ne plus exiger « sortie avant entrée » comme invariant de run. Les anciens contrats préservés restent testés ; les changements R1–R7 sont documentés/versionnés.

### Phase 1 — Verticale agentique texte + image, locale et distante

Livrer la boucle et l'unique adaptateur OpenAI-compatible avec sortie structurée, les budgets aux défauts actés en section 3.4, les observations multimodales et le store opérationnel. Fournir lecture chat, lecture users bornée, écriture chat et une capture simple. Livrer un premier proxy de capture/agent PC pour prouver la topologie serveur + PC, sans distribuer le bus. Fournir les profils de déploiement et les modules/manifests installables correspondants.

Critères de sortie :

- Grants explicitement configurés dans les tests/profils pour chaque action du scénario, lectures et capture comprises. Faux modèle scripté : `chat.read` → observation texte → `screen.capture` → observation image → réponse finale → `chat.write` acquitté. Exactement un envoi, aucun envoi intermédiaire ; IDs continus et référence image nettoyée après run.
- Même scénario via provider local puis proxy sur une frontière de transport simulée : même contrat, aucune branche de capture dans brain. Test réel sur deux processus pour sérialisation, authentification, annulation et accès aux pièces jointes ; essai PC/serveur consigné avant de déclarer cette topologie livrée.
- Backend Chat Completions configuré vérifié pour sortie structurée/tools et vision via l'adaptateur unique ; backend sans capacité requise refusé avant le scénario, sans perte silencieuse des images. Le faux modèle prouve l'orchestration, pas la qualité du modèle réel.
- Absence de règle applicable (même pour `chat.read`), actions interdites/désactivées, arguments invalides, changement de disponibilité pendant run, image trop grosse/expirée, timeout puis réponse tardive/double, annulation à chaque étape, rupture proxy après émission d'un effet : bilan correct, aucun retry aveugle ni fuite de tâches/pièces jointes.
- Tours, actions répétées, tokens, attente, deadline de livraison et saturation arrêtent le travail concerné selon les bornes actées et la politique de repli à trancher en section 7 ; un résultat externe incertain n'est jamais mémorisé comme envoi confirmé.
- `users.read` indique pagination, fraîcheur et couverture ; aucune liste partielle présentée comme audience complète. Une seconde plateforme factice valide l'absence de dépendance à Twitch dans les contrats/brain.
- Installation dans un environnement vierge puis découverte effective des modules/manifests ; smoke test des profils PC et serveur/proxy sans secrets embarqués.

### Phase 2 — Audio et interaction stream

Compléter les sept starters : capture/sortie audio, scènes et sondages ; enrichir les adaptateurs plateforme sans introduire de commande métier dans le core. Vérifier les capacités audio requises de l'adaptateur modèle unique et configurer explicitement les règles/grants avant les effets ; le choix des sources/sorties audio reste dans les modules.

Critères de sortie :

- Capture d'un segment borné → observation audio/transcription → réponse audio ; limites de durée/octets, fin de lecture, interruption et indisponibilité du périphérique vérifiées localement et via proxy.
- Scène autorisée et sondage valide : un effet confirmé et tracé ; permission absente ou cible hors périmètre : zéro effet. Deux sessions concurrentes sur une ressource partagée respectent la sérialisation du provider.
- Perte de confirmation après commande : réconciliation ou `external_unknown`, sans seconde création de sondage ni fausse confirmation. Arrêt pendant capture/lecture/contrôle de scène respecte le budget global.
- Les sept unités sont activables/désactivables avec une vue honnête des capacités ; les dépendances audio/OBS optionnelles n'empêchent pas un profil chat seul. Packaging et essais d'intégration propres à chaque provider documentés.

### Phase 3+ — Seulement selon l'usage observé

| Extension conditionnelle | Déclencheur et critère avant livraison |
| --- | --- |
| Persistance, reprise, mémoire longue | Besoin de survie au redémarrage ; tester crash avant/après effet, idempotence, expiration/effacement et reprise sans faux envoi. |
| Multi-instance du brain, instances multiples de providers, bus distribué éventuel | Charge ou disponibilité mesurée ; prouver ownership exclusif des sessions, fencing, traitement des partitions et absence de double effet. Le proxy PC de phase 1 n'attend pas cette phase. |
| Hot reload | Besoin de reconfiguration sans arrêt ; désinscription par propriétaire, drainage, retrait atomique des tools et absence de références/tâches orphelines. |
| Sandbox de modules | Exécution de code non fiable ; frontière de processus et capacités testées. Aujourd'hui comme en phase 1, un module Python dans le processus reste du code de confiance. |
| Parallélisme d'outils, workflows, résumés/recherche | Gain démontré ; tests de conflits, budgets agrégés, causalité et qualité. Aucun framework, broker ou graphe dynamique imposé au socle. |

## 7. Questions produit à trancher avant chaque phase

**Décisions actées (batch 1) :** défaut de trigger déclaré par type d'input et remplaçable (2.4) ; sessions de runs/mémoire par plateforme + canal + viewer, contexte de chat séparé par plateforme + canal (2.2, 3.4–3.5) ; FIFO bornée par session avec deadline, `stale_drop` et rejet du nouveau travail si pleine (3.4) ; valeurs par défaut des budgets acceptées et configurables (3.4) ; aucune action auto-autorisée, refus par défaut et règles du streamer et/ou des modules (3.2, 4.1) ; adaptateur modèle unique OpenAI-compatible Chat Completions, capacités vérifiées et backend incompatible refusé (3.1). Ces décisions ne sont plus à arbitrer.

| Décision attendue | Échéance et conséquence |
| --- | --- |
| Comportement si dépassement de budget/expiration (4b) | Avant phase 0 pour admission/arrêt et attente ; avant phase 1 pour tours/tokens/médias : silence total, message de repli générique désactivable ou repli conditionnel. **Proposition : message de repli générique désactivable.** Toute sortie de repli reste soumise aux autorisations et au budget de sortie, sans prolonger la deadline ni répondre au contenu devenu obsolète. |
| Détail du système de permissions | À définir plus tard, avant son implémentation : format des règles, combinaisons des règles streamer/modules et portée canal/session. Phase 1 : grants explicites dans tests/profils pour lecture/envoi/capture ; phase 2 : règles explicites pour audio, scène et sondage. Le refus par défaut et le rôle déclaratif des modules sont actés, aucune catégorie d'action auto-autorisée n'est à choisir. La modération exige une décision propre avant ajout de tools. |
| Rétention après stream/redémarrage et accès aux données (8) | Avant phase 0 pour TTL locaux ; avant phase 1 pour médias ; avant toute persistance pour effacement/reprise. **Proposition : tout volatile + TTL courts + audit fichier optionnel rotatif.** Durées, accès et modalités de conservation/effacement restent à trancher ; aucune conservation durable implicite. |
| Profils de déploiement et premier transport proxy (9) | Avant phase 1 : installation PC/serveur, connexion de l'agent PC, appairage et résilience. **Proposition : brain serveur ou PC + agent PC proxy WebSocket JSON avec token, un agent par brain.** Le support local et distant est acté ; les moyens concrets restent à choisir et à tester. |

**Configuration des modules, hors arbitrages du cœur :** source de capture (OBS/OS/jeu), zone, cadence et sources/sorties audio appartiennent à chaque module et à sa configuration, avec la mécanique dans le module, jamais dans brain. Ces choix ne sont ni une décision produit globale ni un gate de phase pour le core. La capture est naturellement action-driven, à la demande d'observation du brain, sans capture de fond ; toute capture reste soumise aux règles d'autorisation explicites et aux budgets. Les scénarios de phase utilisent une source configurée pour vérifier le contrat, sans imposer cette source au produit.
