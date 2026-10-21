# Déclencheurs — guide opérateur

[English](triggers.md) · **Français**

> Ce document est la traduction de [`docs/triggers.md`](triggers.md) : **le texte anglais fait foi**
> là où les deux diffèrent.

Ce guide est la référence des types de déclencheurs qu'une entrée déclare et des quatre familles de
déclencheurs qu'apporte la phase 8 : les points de chaîne, le bouton de l'opérateur, la voix du
streamer et un déclencheur aléatoire temporisé.

Un déclencheur décide une seule chose : si un événement reçu par une entrée devient un *candidat* à une
exécution. Il n'accorde rien. Un candidat accepté passe encore par l'admission, les budgets et
l'autorisation des actions refusée par défaut, exactement comme un message du chat, et chacun d'eux peut
encore le refuser. Tout ce qui suit est éprouvé par la suite de tests contre des faux uniquement — une
fausse plateforme, de fausses surfaces, un faux détecteur, un faux enregistreur et une horloge injectée.
Aucun test ne contacte Twitch, n'ouvre de socket ni ne lit un microphone. Une instance réelle n'est
éprouvée que par l'essai manuel décrit à la fin de ce guide.

## Types génériques et types déclarés

Chaque entrée déclare, dans le bloc `triggers` de son `module.yaml`, les types de déclencheurs par
lesquels ses événements peuvent être retenus. Il en existe deux sortes.

- **Les types génériques.** Quatre types que toute entrée peut déclarer, chacun restreignant le schéma
  canonique du noyau : `probability` (un tirage sous une probabilité configurée), `audience` (qui a
  écrit le message), `keyword` (le texte contient l'un des mots-clés configurés) et `event_kind` (le type
  de l'événement figure dans la liste). Une entrée peut restreindre un type générique — `operator`
  limite `event_kind` à `manual` — mais jamais l'élargir.
- **Les types déclarés.** Un type qu'une entrée définit pour ses propres événements : un `name`
  (`[a-z][a-z0-9_]*`, 32 caractères au plus, aucun des quatre noms génériques), un `parameter_schema`,
  la liste non vide des `kinds` auxquels il s'applique, et exactement l'un des deux : une règle
  déclarative `match` ou `hook: true`. Le noyau évalue lui-même une règle `match` : aucun code de module
  ne s'exécute sur le chemin critique pour elle.

Un type déclaré restreint et n'élargit jamais. Sa règle ne s'exécute que sur un événement publié par
l'entrée qui l'a déclaré ; une politique qui le nomme pour une autre entrée est refusée au chargement.
Un événement d'un type hors des `kinds` du type ne correspond pas (`<type>_kind_mismatch`). Chaque
invariant qui passe avant les déclencheurs aujourd'hui — exclusion des traces internes, normalisation,
authenticité, anti-écho, fenêtre de déduplication — passe toujours en premier, inchangé.

Une erreur de configuration dans un type déclaré ou dans une politique arrête le démarrage avant
l'ouverture de tout transport, avec un diagnostic qui nomme l'entrée et le chemin du champ fautif (par
exemple `triggers.timer.policy.rules[0].parameters.window`) et ne reprend aucune valeur configurée.

## Le vocabulaire de correspondance

Une règle `match` est un arbre d'un ensemble fermé de nœuds : `all`, `any`, `not`, `equals`, `in`,
`gte`, `lte` et `matches`. Il compte au plus 8 niveaux et au plus 64 nœuds. Ses feuilles sont des
opérandes :

- `{field: <path>}` — une valeur de la charge utile de l'événement. Le chemin doit être un champ
  normalisé canonique (`kind`, `text`, `platform`, `channel_id`, `message_id`, `author.id`,
  `author.display_name`) ou un champ que l'entrée déclare dans `triggers.fields` avec son type scalaire.
  Un type ne peut pas affirmer un champ que son entrée n'émet pas : une règle qui nomme un champ non
  déclaré est refusée au chargement (**A4**, le catalogue de champs).
- `{param: <name>}` — un paramètre configuré du type, ou un chemin à points dans un paramètre objet
  (`window.start_minute`). Une comparaison dont le paramètre est absent de la configuration est vraie :
  une borne facultative omise ne restreint donc rien. Un paramètre non requis ne peut pas être utilisé
  sous `not`.
- Un littéral : une chaîne, un nombre, un booléen, ou une liste d'au plus 64 tels scalaires.

Les comparaisons suivent les types JSON. `equals`, `gte`, `lte` et `matches` sont faux quand le champ
est absent ou d'un autre type que l'autre opérande. `in` est vrai exactement quand la liste contient un
élément du même type égal à la valeur. Les chaînes se comparent en respectant la casse, sauf sous
`matches`.

**`matches` est un joker, pas une expression régulière (A2).** Le motif ignore la casse, `*` représente
une suite quelconque de caractères et `?` un caractère, et un motif compte au plus 128 caractères. Une
expression régulière venue de la configuration et évaluée sur le texte des spectateurs est une surface
de déni de service ; le comparateur à jokers fait au contraire au plus (len(text) + 1) × (len(pattern) +
1) pas, un pas étant une comparaison d'une position du motif avec une position du texte. Le moteur les
compte, et ses compteurs exposent le total sous `wildcard_steps`.

**Un paramètre de probabilité (A3).** Le vocabulaire n'a pas d'opérateur aléatoire. Un type déclaré peut
désigner l'un de ses propres paramètres comme son `probability_parameter` : un nombre requis compris dans
[0, 1]. Chaque évaluation de la règle tire alors exactement une valeur de la source aléatoire injectée
du moteur — que l'arbre corresponde ou non — et la règle ne correspond que si l'arbre correspond et que
le tirage est sous la probabilité configurée (sinon `<type>_probability_miss`). Chaque tirage est
consigné dans les `draws` de la décision : l'indice de la règle, le nom du paramètre, sa valeur, le
tirage et son ordinal, de sorte qu'une exécution enregistrée peut être rejouée.

Les raisons qu'une règle déclarée produit sont `<type>_match`, `<type>_no_match`,
`<type>_kind_mismatch` et `<type>_probability_miss`, préfixées par `accepted:` ou `rejected:`.

## Types à crochet

Certaines décisions ne s'écrivent pas comme une règle de correspondance. Un type déclaré avec
`hook: true` confie sa correspondance à une fonction que son propre module fournit à l'activation ;
aucun autre module ne peut la fournir. Le crochet est borné et échoue fermé :

- C'est une fonction coroutine, attendue par le point d'entrée asynchrone du moteur et mise en
  concurrence avec l'horloge du moteur pendant le `hook_timeout_seconds` du type, compris dans ]0, 10].
  Le point d'entrée synchrone n'appelle jamais de crochet : il décide `<type>_hook_unavailable` à la
  place, car un crochet n'est atteignable que là où il peut être borné. Un crochet qui bloque la boucle
  d'événements de façon *synchrone* dans sa coroutine ne peut pas être interrompu dans le processus : un
  crochet doit donc coopérer ; le crochet `wake_word` livré est pur et en temps constant.
- Il reçoit une vue en lecture seule de l'événement et des paramètres de la règle, et il est appelé au
  plus une fois par évaluation — jamais pour un événement rejeté par un invariant, rappelé de la fenêtre
  de déduplication, d'un type hors des `kinds` du type, ou publié par une autre entrée.
- `True` correspond (`<type>_match`) et `False` ne correspond pas (`<type>_no_match`). Tout le reste
  signifie **aucun déclenchement** : une autre valeur de retour ou une exception (`<type>_hook_error`),
  aucune réponse dans la borne (`<type>_hook_timeout`), aucun crochet fourni
  (`<type>_hook_unavailable`).
- Un crochet en échec est un veto absolu. Une fois toutes les règles de la politique évaluées, et en
  dehors de la combinaison de la politique, un échec de crochet rejette toute la décision avec la raison
  de la première règle en échec — quelle que soit la combinaison (`all_of`, `any_of`, `none_of`) et
  quoi d'autre ait correspondu. Aucune combinaison, polarité ou valeur par défaut ne peut transformer un
  crochet en échec en acceptation.

Chaque résultat de crochet est consigné comme toute autre décision : compté, traçable et conservé dans
la fenêtre de déduplication. Un crochet ne peut ni créer d'exécution ni modifier l'admission, les
budgets ou l'autorisation, et son échec n'interrompt jamais la réception des événements suivants.

## Configurer une politique

Un profil choisit une politique par entrée et par chaîne dans son bloc `triggers` de premier niveau.
Une politique configurée remplace entièrement la politique par défaut. Les règles ne sont jamais
fusionnées avec celle qu'elles remplacent ; une chaîne sans entrée propre garde la politique par défaut
que déclare le manifeste de l'entrée. Une politique a une `combination` (l'une de celles que l'entrée
prend en charge) et une liste de `rules`, chacune nommant un type que l'entrée déclare et les
`parameters` que son schéma accepte.

Chaque exemple ci-dessous est un fragment : la partie `modules` va sous le `modules` de votre profil, à
côté des autres réglages de ce module, et le module va dans `enabled_modules` ; la partie `triggers` va
dans le bloc `triggers` de votre profil. Chaque exemple est chargé par la suite de tests contre les
manifestes livrés : il reste donc valide quand les manifestes évoluent. Les références `${...}` sont des
variables d'environnement, comme partout dans un profil.

## Points de chaîne : `reward`

Quand `notices.kinds` liste `reward`, l'entrée `twitch` ajoute l'abonnement aux échanges de points de
chaîne (qui demande, pour le diffuseur, la portée de lecture des échanges). Chaque échange devient un
événement de type `reward` : son `text` est la saisie du spectateur quand il en a tapé une et le titre
de la récompense sinon, et `payload.reward` porte `{id, title, cost}`. Un abonnement refusé ne dégrade
que `reward` ; le chat continue. La politique par défaut de `twitch` est inchangée : c'est la règle
`keyword` sur le nom du compagnon, et `keyword` lit le `text` de l'événement quel que soit son type.
Dès que `reward` est listé, un échange dont la saisie du spectateur — ou, à défaut, le titre de la
récompense — contient le nom du compagnon déclenche donc sous la politique par défaut, quelles que
soient la récompense et son coût. Tout autre échange ne déclenche rien tant que vous ne configurez pas
le type `reward`.

Le type `reward` prend `reward_ids` (requis : les identifiants des récompenses qui peuvent déclencher),
et les facultatifs `min_cost` et `max_cost` (entiers ≥ 0, bornes incluses). Une politique configurée
remplace la politique de chat par défaut pour cette chaîne : pour continuer à répondre aux mentions,
ajoutez la règle `keyword` avec `any_of`, comme ci-dessous.

```yaml
modules:
  twitch:
    notices:
      kinds: [reward]

triggers:
  twitch:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: any_of
        rules:
          - type: reward
            parameters:
              reward_ids: ["ask-the-companion"]
              min_cost: 100
              max_cost: 5000
          - type: keyword
            parameters:
              keywords: ["Companion"]
```

Ici aussi, la règle `keyword` lit le `text` de tout événement, échanges compris : l'échange d'une
récompense absente de `reward_ids`, ou hors des bornes de coût, déclenche quand même si sa saisie ou son
titre contient « Companion ». Les bornes de `reward` limitent ce qu'accepte la règle `reward`, pas la
politique.

Honorer ou rembourser un échange est hors du périmètre : le compagnon ne change jamais son statut.

## Le bouton de l'opérateur : `button`

L'entrée `operator` publie un événement de type `manual` par appui accepté sur un bouton configuré. Le
`text` de l'événement est le `prompt_text` du bouton, son auteur est `operator:<id>` avec le libellé du
bouton, et `payload.button` porte l'`id` du bouton et la `surface` d'où il a été pressé. Son exécution
répond sur la `destination` configurée. Streamer.bot n'est ni requis ni touché.

Il y a trois surfaces, chacune désactivée tant qu'elle n'est pas activée dans `surfaces`, et au moins
une doit l'être :

- **`page`** — une page locale listant les boutons, à `http://<host>:<port>/?token=<token>`.
- **`overlay`** — une page pour une source navigateur, à `http://<host>:<port>/overlay?token=<token>`.
  Elle affiche l'`overlay.image` facultative avec chaque bouton qui a un `rect` placé dessus, en
  pourcentage de l'image. Chaque clic est renvoyé au compagnon.
- **`hotkey`** — la commande d'appui livrée, `python -m modules.operator press <button>`.

L'écoute se lie à `127.0.0.1` par défaut ; une autre adresse demande `allow_non_loopback`, et une
adresse joker est refusée. Chaque requête porte le `token` (32 caractères au moins, une référence
d'environnement), comparé en temps constant, et elle est refusée sans lui ; le jeton n'apparaît jamais
dans un événement, une trace, un diagnostic ni un journal d'accès. Un appui sur un bouton inconnu,
depuis une surface désactivée, ou dans le `cooldown_seconds` du bouton (2 par défaut) après son dernier
appui accepté est refusé et compté. L'écoute n'existe qu'entre `start_inputs` et `stop_inputs`.

La politique par défaut accepte tout appui (`event_kind` avec `kinds: [manual]`). Le type `button`
limite une chaîne aux `button_ids` listés :

```yaml
modules:
  operator:
    buttons:
      - id: hype
        label: Hype
        prompt_text: "Cheer the chat on for the last play."
        cooldown_seconds: 30
        rect: {x: 80, y: 5, w: 15, h: 10}
      - id: recap
        label: Recap
        prompt_text: "Give the chat a short recap of the stream so far."
    destination:
      platform: twitch
      channel_id: ${TWITCH_BROADCASTER_ID}
    surfaces:
      page: true
      hotkey: true
      overlay: true
    listen:
      host: 127.0.0.1
      port: 8765
    token: ${OPERATOR_TOKEN}

triggers:
  operator:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: button
            parameters:
              button_ids: ["hype", "recap"]
```

**Associer un raccourci global.** Le compagnon n'associe aucune touche : un raccourci global demande des
API du système d'exploitation que le noyau ne prend pas en charge. C'est la commande d'appui que vous
associez, avec l'outil que votre système propose pour cela — les raccourcis personnalisés d'un
environnement de bureau, un démon de raccourcis, le logiciel d'un pavé de macros, ou une action de
stream deck qui lance un programme. Associez la touche à la ligne de commande ci-dessous, lancée depuis
l'environnement virtuel du dépôt avec la variable d'environnement que nomme le `token` du profil :

```bash
python -m modules.operator press hype --profile /path/to/my-config.yaml
```

Elle lit le profil (`config.yaml` par défaut), prend l'adresse `listen` de l'opérateur et ne résout que
la référence `${NAME}` du jeton, envoie un appui avec la surface `hotkey`, et n'affiche aucun jeton.
Elle sort avec `0` quand l'entrée a accepté l'appui, `2` quand l'entrée l'a refusé (bouton inconnu,
délai, jeton, surface désactivée), `3` quand l'entrée est injoignable ou n'a pas répondu en 5 secondes,
et `64` sur une erreur d'usage ou de profil.

## La voix du streamer : `keyword` et `wake_word`

`audio_input` devient une entrée quand `listen.enabled` vaut `true` (`false` par défaut : écoute
coupée, aucun détecteur ni aucune source ne démarre jamais et le module se comporte comme avant).
L'écoute demande un détecteur de mot d'éveil configuré, `wake_word.detector` ; aucun n'est embarqué et
aucun n'est supposé :

- `{kind: command, argv}` — un programme lancé sans aucun audio venant du compagnon. Il attend sur son
  propre microphone et sort avec `0` et la phrase reconnue (64 caractères au plus) sur sa sortie
  standard quand il détecte, ou avec `1` quand il rend la main sans détecter.
- `{kind: http, endpoint, api_key}` — une requête par invocation, sans aucun audio. Il répond
  `{"detected": true, "phrase": "..."}` ou `{"detected": false}`. `api_key` est facultative.

Chaque itération suit un seul ordre. (1) Le détecteur rend un verdict : détecté, et la phrase. (2) Le
module soumet au moteur de déclencheurs un événement *candidat* de type `speech` : son `text` est la
phrase du détecteur (jamais une transcription) et `payload.wake_word` vaut `{detected: true, phrase}`.
Le candidat n'est jamais publié. (3) Seulement si la politique l'accepte, le module acquiert un segment
auprès de la source `command` que nomme `listen.source`, d'au plus `listen.max_seconds` et commençant
au plus `listen.pre_roll_seconds` avant le verdict, et le transcrit une fois par les réglages
`transcription` existants. (4) L'exécution reçoit un événement `speech` dont le `text` est cette
transcription, admis sous la décision du candidat. Un candidat refusé n'acquiert et ne transcrit rien.

Les deux types de déclencheurs travaillent donc **sur la phrase du détecteur**, pas sur une
transcription. Le type à crochet `wake_word` (sans paramètre, borne de 2 secondes) est la politique par
défaut : il correspond quand le détecteur a signalé une détection. Le type générique `keyword`
correspond quand la phrase contient l'un des mots-clés configurés : il choisit donc *quelles* phrases
d'éveil lancent une exécution. Dans l'exemple, une exécution ne démarre que si le détecteur a signalé une
détection et que sa phrase contient « hey companion » :

```yaml
modules:
  audio_input:
    listen:
      enabled: true
      source: mic
      pre_roll_seconds: 2
      max_seconds: 10
      speaker: streamer
      destination:
        platform: twitch
        channel_id: ${TWITCH_BROADCASTER_ID}
    wake_word:
      detector:
        kind: command
        argv: ["${WAKE_WORD_DETECTOR}"]

triggers:
  audio_input:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: wake_word
            parameters: {}
          - type: keyword
            parameters:
              keywords: ["hey companion"]
```

**La source d'écoute n'est pas un enregistreur de capture.** `listen.source` nomme une source
`command` sous `audio_input.sources`, mais l'écoute la lance autrement qu'`audio.capture` : rien ne
l'arrête au bout d'une durée, elle doit donc terminer le segment elle-même. Elle est lancée avec
`AUDIO_INPUT_PRE_ROLL_SECONDS` (= `listen.pre_roll_seconds`) et `AUDIO_INPUT_MAX_SECONDS`
(= `listen.max_seconds`) dans son environnement, et doit écrire sur sa sortie standard un WAV 16 kHz
mono 16 bits qui commence au plus ce pré-enregistrement avant le verdict et dure au plus ce maximum,
avec un en-tête dont les tailles indiquent les données réellement écrites (pas une valeur provisoire de
flux), puis fermer sa sortie et sortir d'elle-même avec `0`. Toute autre issue — un autre code de
sortie, un en-tête qui n'indique pas les données, un segment plus long, plus de `max_bytes` — est un
échec de la source, jamais tronqué. La source `mic` des profils livrés est un enregistreur de
capture qui diffuse jusqu'à ce qu'on l'arrête : l'exemple ci-dessus nomme `mic`, mais avec cet
enregistreur inchangé chaque mot d'éveil accepté finit en échec de la source. Faites pointer `mic` (ou
la source que nomme `listen.source`) vers un programme qui respecte les deux variables — typiquement un
script lisant le tampon circulaire de votre microphone — avant d'activer l'écoute.

Un échec du détecteur — un programme qui ne démarre pas, sort autrement qu'avec `0` ou `1`, ou signale
une phrase de plus de 64 caractères ; un point d'accès injoignable ou qui répond sous une autre forme —
ne se lit jamais comme « pas de mot d'éveil ». Il signale `audio_input` dégradé une fois
(`wake_word_detector_failed`), et le rétablissement est signalé une fois par `module.ready`. Le
détecteur redémarre 1 seconde après un candidat refusé ou un événement `speech` publié, et
`retry_seconds × 2^(n−1)` secondes (300 au plus) après la n-ième itération improductive d'affilée — pas
de détection, un échec du détecteur, un échec de la source, une transcription en échec ou vide — de sorte
qu'un détecteur mort ne tourne jamais en boucle. L'audio du segment n'atteint jamais le bus, le flux
d'audit ni le magasin de pièces jointes.

## Un déclencheur aléatoire temporisé : `interval`

L'entrée `timer` publie un événement de type `timer` par chaîne configurée toutes les `every_minutes`
minutes, sur l'horloge du compagnon, à compter de `start_inputs`. Son `text` est le `prompt_text` de la
chaîne. Le type déclaré `interval` décide de chaque déclenchement : `every_minutes` (requis, 1–1440),
`probability` (requis, 0–1, son paramètre de probabilité : un tirage par déclenchement) et une
`window` facultative (`start_minute` et `end_minute`, 0–1439 en heure locale de l'hôte, début avant
fin, bornes incluses). Sans fenêtre, toutes les minutes de la journée conviennent. La politique par
défaut est `interval{every_minutes: 30, probability: 0.25}`. La politique résolue d'une chaîne doit
contenir exactement une règle `interval`.

```yaml
modules:
  timer:
    channels:
      - destination:
          platform: twitch
          channel_id: ${TWITCH_BROADCASTER_ID}
        prompt_text: "Say something about what is on screen right now."

triggers:
  timer:
    channels:
      ${TWITCH_BROADCASTER_ID}:
        combination: all_of
        rules:
          - type: interval
            parameters:
              every_minutes: 20
              probability: 0.5
              window:
                start_minute: 1080
                end_minute: 1380
```

Ici un déclenchement est dû toutes les 20 minutes ; entre 18 h et 23 h, chacun devient un candidat avec
une probabilité d'une chance sur deux.

## Hors périmètre : l'écoute continue

L'écoute continue est hors du périmètre. Le compagnon ne transcrit jamais selon un calendrier ni en
continu, ne diffuse jamais l'audio en continu vers un transcripteur ou vers le noyau, et n'écoute
jamais par défaut. Le coût de l'écoute est borné par le mot d'éveil, pas par le temps : un verdict du
détecteur, puis au plus une acquisition et une transcription par candidat accepté. Comparer la
transcription complète de chaque énoncé avant de décider reviendrait à transcrire tout ce qui est dit.
Un futur greffon pourra le proposer, délibérément optionnel et délibérément coûteux, sans changer le
contrat ci-dessus. L'audio n'est stocké que lorsqu'une exécution appelle `audio.capture`, exactement
comme avant.

## Essai manuel sur une instance réelle

La suite éprouve chaque famille contre des faux uniquement. La vérification sur une instance réelle est
une procédure manuelle qu'un opérateur mène sur sa propre chaîne, et son compte rendu est
`docs/campaigns/phase8/trial-record.md` (statut `not run` tant qu'aucun opérateur ne l'a mené). Aucun
test n'effectue l'une de ces étapes.

1. Partez d'un profil qui fonctionne pour le chat. Ajoutez les quatre fragments ci-dessus, adaptés à
   votre chaîne, puis validez le profil sans ouvrir de socket :

   ```bash
   python -m core.main --config my-config.yaml --check-config
   ```

2. Lancez le compagnon avec `python -m core.main --config my-config.yaml` et gardez sa sortie de traces.
3. **Points de chaîne.** Accordez la portée de lecture des échanges, créez une récompense dont vous avez
   configuré l'identifiant, et échangez-la depuis un compte spectateur. Échangez ensuite une récompense
   dont vous n'avez pas configuré l'identifiant.
4. **Bouton.** Pressez chaque bouton configuré depuis la page, depuis l'overlay chargé comme source
   navigateur, et depuis la touche associée à la commande d'appui. Pressez-en un à nouveau pendant son
   délai.
5. **Voix.** Dites la phrase d'éveil, puis une phrase que la règle `keyword` ne liste pas, puis arrêtez
   le programme détecteur et relancez-le.
6. **Minuterie.** Attendez deux déclenchements dans la fenêtre et un en dehors.
7. Pour chaque famille, consignez dans le compte rendu : la date, la version (commit) et la plateforme,
   ce qui a été déclenché, un extrait de la trace de décision (la raison, jamais un jeton ni une clé), et
   le résultat — l'exécution a eu lieu, a été refusée, ou rien n'a été décidé. Renseignez le statut du
   compte rendu une fois chaque famille essayée.
