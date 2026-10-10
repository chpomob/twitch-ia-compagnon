# Protocole de proxy v1 — cerveau ⇄ agent PC en JSON sur WebSocket

[English](proxy-protocol.md) · **Français**

> **Traduction de courtoisie.** Ce document est la traduction du protocole normatif
> [`docs/proxy-protocol.md`](proxy-protocol.md) : **la version anglaise fait foi**, et c'est elle que la
> suite de tests vérifie contre les constantes de `core/contracts.py` (`tests/test_proxy.py`). En cas
> d'écart entre les deux textes, l'anglais a raison et cette traduction est fausse.

**Statut : normatif.** Ce document fixe le protocole v1 entre le module côté cerveau
`modules/proxy` (serveur, écoute) et le module côté agent `modules/agent_link` (client, compose le
numéro), comme décidé par la spécification de la phase 1 (`docs/campaigns/phase1/spec.md`, R6 et
décisions 6–9) et par la conception v2 (`docs/design-v2.md`, section 4.3). Là où ce document et la
spécification diffèrent, la spécification fait foi et ce document est faux. Chaque type de trame,
code d'erreur, code d'`attachment_ack` et code de fermeture ci-dessous est orthographié exactement
comme les constantes de `core/contracts.py` (section « Proxy protocol v1 ») ; `tests/test_proxy.py`
vérifie qu'ils ne divergent pas.

Vocabulaire : le **cerveau** est le processus qui exécute `modules/proxy` ; l'**agent** est le
processus qui exécute `modules/agent_link` avec les modules de capacité locaux qu'il expose
(`capture` en phase 1). Le **bus n'est pas distribué** : aucune souscription, aucune trace et aucun
état de run ne traverse le fil ; seuls des appels d'action, leurs observations, des pièces jointes,
des annulations, un battement de cœur et un type d'événement en liste blanche le font.

## 1. Transport

- Une connexion WebSocket par agent, ouverte par l'agent vers `brain_url`. L'agent fonctionne
  derrière un NAT ; le cerveau ne compose jamais le numéro.
- Exactement **un agent appairé par cerveau** à tout instant (décision 6). Un second agent qui
  s'authentifie alors qu'un autre est appairé est refusé (`agent_limit`, §7).
- Chaque **trame de texte** est un objet JSON UTF-8 portant l'enveloppe du §2. Une trame de texte qui
  n'est pas un objet JSON, ou dont l'enveloppe est mal formée, reçoit `error invalid_frame` (§7).
- Les **trames binaires** n'existent que pour une chose : la charge utile d'une pièce jointe,
  immédiatement après son en-tête `attachment` (§10). Une trame binaire reçue à tout autre moment
  reçoit `attachment_ack` avec `accepted: false` et le code `unexpected_binary`, et ses octets sont
  jetés.
- Les règles TLS sont au §14 ; le jeton d'appairage est au §4.

## 2. Enveloppe

Chaque trame de texte porte exactement ces trois champs au premier niveau, plus les champs que son
type définit (§5) :

| Champ  | Type                | Règle |
| ------ | ------------------- | ----- |
| `v`    | entier              | Version du protocole ; **doit valoir `1`** (`PROXY_PROTOCOL_VERSION`). Toute autre valeur, ou un non-entier, reçoit `error unsupported_protocol_version` et la connexion est fermée avec le code `4400`. |
| `type` | chaîne              | L'un des onze types de trame de texte du §5. Un `type` inconnu reçoit `error unknown_frame` ; la **connexion est conservée**. |
| `id`   | chaîne, ou `null`   | Identifiant de corrélation (§3). `null` n'est légal **que** sur une trame `error` qui ne répond à aucune trame identifiable. |

Les champs supplémentaires inconnus sont ignorés par le destinataire (compatibilité ascendante à
l'intérieur de la v1) ; un champ requis manquant, ou un champ du mauvais type, est un
`invalid_frame`.

## 3. Corrélation

La corrélation est portée par `id` et dépend de la portée de la trame.

| Portée         | Types de trame                                          | Valeur de `id` |
| -------------- | ------------------------------------------------------- | -------------- |
| Appairage      | `hello`                                                | Une **chaîne non vide** tirée par l'agent (nonce). Un `hello` dont l'`id` est manquant, non-chaîne ou vide reçoit `error invalid_frame` et la connexion est fermée `4400` (AC48). |
| Appairage      | `welcome`                                              | Le **même `id` que le `hello` auquel il répond** (le nonce), pour que l'agent puisse rattacher la réponse à sa propre tentative. `welcome` porte en outre le `session_id` délivré par le serveur. |
| Session        | `ping`, `pong`, `event`, `attachment`, `attachment_ack` (`PROXY_SESSION_FRAME_TYPES`) | `id` = le `session_id` courant. Une trame de portée session dont l'`id` diffère du `session_id` courant reçoit `error unknown_session` et n'est **pas traitée** (un `ping` avec `id: "other"` produit 0 `pong`). |
| Appel          | `call`, `observation`, `cancel` (`PROXY_CALL_FRAME_TYPES`) | `id` = le `call_id` de l'`ActionCall` (§5.4). |
| Réponse        | `error`                                                | L'`id` de la trame à laquelle elle répond, ou **`null`** quand aucune ne s'applique (par exemple une trame illisible, ou un `ping` reçu avant tout `hello`). |

`session_id` est une chaîne non vide générée par le cerveau pour chaque appairage réussi et jamais
réutilisée pour un appairage ultérieur du même processus cerveau. À partir de `welcome`, c'est le
seul `id` accepté pour les trames de portée session jusqu'à la chute de la connexion.

**Avant `welcome`, le cerveau n'accepte que `hello`.** Toute autre trame reçue avant l'appairage
reçoit `error unknown_session` (avec `id: null`) et n'est pas traitée ; la connexion reste ouverte
pour que l'agent puisse encore envoyer son `hello` (AC48).

## 4. Appairage : `hello` / `welcome`

1. L'agent envoie `hello` avec son `agent_id`, le `token` d'appairage, les `actions[]` qu'il sert et
   son propre `max_frame_bytes`.
2. Le cerveau compare `token` au jeton d'appairage configuré **en temps constant**. En cas d'écart,
   il envoie `error auth_failed` et ferme `4401` ; 0 action ne devient prête (AC32).
3. Si un autre agent est déjà appairé, le cerveau envoie `error agent_limit` et ferme `4409` ; le
   premier agent reste connecté (AC32).
4. Sinon, le cerveau compare chaque action déclarée à la spécification qu'il a prise dans son
   catalogue pour les noms de sa liste blanche `actions` (décision 7) : la spécification découverte
   par le registre lorsqu'un manifeste activé déclare l'action, sinon la déclaration du manifeste
   d'un module désactivé — `capture` sur le serveur, où `screen.capture` n'est servi que
   distant. Une déclaration est **identique** lorsque le nom, la version, les schémas d'arguments et
   de résultat, la nature, les destinations supportées — et, faisant partie de la spécification
   comparée, la capacité optionnelle `delivery` et le drapeau optionnel `model_proposable` (§18) —
   sont égaux. Une déclaration identique rend cette action **prête** ; une déclaration différente est
   exclue avec `error action_mismatch` (`id` = le nonce du `hello`) et laisse les autres ; une action
   déclarée absente de la liste blanche est ignorée.
5. Le cerveau répond `welcome` en portant le nonce comme `id`, le `session_id`, les noms d'actions
   acceptés et ses limites (§6). L'agent réinitialise son état par session à chaque `welcome` (§8) et
   son repli (§13).

L'authentification du pair n'accorde rien : un agent appairé exécute encore chaque `call` à travers
son propre exécuteur en refus par défaut (§9), et le cerveau vérifie encore ses propres règles
d'autorisation avant d'émettre un `call`.

## 5. Types de trame

Le protocole v1 a **douze types de trame** : les onze types de trame de texte déclarés par
`PROXY_FRAME_TYPES`, plus la trame binaire de pièce jointe. La direction « A→B » signifie agent vers
cerveau, « B→A » cerveau vers agent.

| `type`           | Direction | Portée   | Rôle |
| ---------------- | --------- | -------- | ---- |
| `hello`          | A→B       | appairage | S'authentifier et déclarer les actions servies. |
| `welcome`        | B→A       | appairage | Accepter l'appairage, délivrer `session_id`, publier les limites. |
| `error`          | les deux  | réponse  | Refuser ou mettre en échec une trame avec un code. |
| `call`           | B→A       | appel    | Transmettre un `ActionCall`. |
| `observation`    | A→B       | appel    | Retourner l'`ActionObservation` d'un `call`. |
| `cancel`         | B→A       | appel    | Annulation au mieux d'un `call` en vol. |
| `attachment`     | A→B       | session  | En-tête d'un transfert de pièce jointe ; suivi d'exactement une trame binaire. |
| *(trame binaire)* | A→B      | session  | La charge utile de la pièce jointe, exactement `size` octets. |
| `attachment_ack` | B→A       | session  | Accepter ou refuser le transfert. |
| `event`          | A→B       | session  | Un événement en liste blanche pour le bus local du cerveau (§15). |
| `ping`           | les deux  | session  | Demande de battement de cœur (§12). |
| `pong`           | les deux  | session  | Réponse de battement de cœur. |

### 5.1 `hello` (agent → cerveau)

| Champ             | Type            | Sens |
| ----------------- | --------------- | ---- |
| `agent_id`        | chaîne non vide | Identifiant stable de l'agent ; devient `metadata.provider_id` des événements transmis (§15). |
| `token`           | chaîne          | Le jeton d'appairage. Jamais journalisé, jamais tracé, jamais renvoyé. |
| `actions`         | tableau d'objets | Déclarations de forme ActionSpec : `name`, `version`, `description`, `argument_schema`, `result_schema`, `nature`, `required_permissions`, `supported_destinations` (objets `platform`, `channel_id`, `scope`), `timeout_seconds`, `idempotency`, `delivery` lorsque l'action déclare la capacité de livraison, et `model_proposable: true` lorsque l'action est proposable au modèle (§18). |
| `max_frame_bytes` | entier positif  | La plus grande trame de texte que l'agent accepte. |

### 5.2 `welcome` (cerveau → agent)

| Champ        | Type             | Sens |
| ------------ | ---------------- | ---- |
| `session_id` | chaîne non vide  | Identifiant de session délivré par le serveur (§3). |
| `actions`    | tableau de chaînes | Les noms d'actions acceptés (prêts) — un sous-ensemble de la déclaration du `hello`. |
| `limits`     | objet            | Limites du cerveau, au moins `max_frame_bytes` et `max_attachment_bytes`, plus `heartbeat_seconds` et `heartbeat_timeout_seconds` (§6, §12). |

### 5.3 `error` (l'une ou l'autre direction)

| Champ       | Type    | Sens |
| ----------- | ------- | ---- |
| `code`      | chaîne  | L'un des codes du §7 (`PROXY_ERROR_CODES`). |
| `message`   | chaîne  | Lisible par un humain, **assainie** : jamais un jeton, un justificatif d'identité, un secret configuré, un chemin de fichier ni la charge utile fautive. |
| `retryable` | booléen | Si la même trame peut raisonnablement réussir plus tard (§7). |

### 5.4 `call` (cerveau → agent)

Les champs d'`ActionCall` de `core/contracts.py`, portés tels quels, plus les quatre champs de
transport. `id` est le `call_id`.

| Champ                 | Type             | Sens |
| --------------------- | ---------------- | ---- |
| `action_name`, `action_version`, `arguments`, `conversation_id`, `run_id`, `call_id`, `source_event_id`, `principal`, `message_id`, `contract_version` | comme `ActionCall` | Transmis sans modification. `arguments` est un objet JSON déjà validé par l'exécuteur du cerveau contre la spécification du catalogue ; l'exécuteur de l'agent le valide à nouveau. |
| `destination`         | objet            | `platform`, `channel_id`, `scope` — une `Destination` concrète. |
| `seq`                 | entier positif   | Numéro de séquence par session, strictement croissant à partir de `1` au premier `call` après `welcome` (§8). |
| `deadline_utc`        | chaîne           | Instant UTC ISO-8601 de l'échéance du cerveau. **Informatif seulement** (§9). |
| `remaining_ms`        | entier positif ou nul | Millisecondes restantes sur l'horloge monotone du cerveau quand la trame a été construite. **C'est ce qui borne l'agent** (§9). |
| `max_attachment_bytes`| entier positif   | La plus grande pièce jointe unique que le cerveau accepte pour cet appel (§6). |

Le flottant monotone `ActionCall.deadline` n'est **pas** transporté : il n'a aucun sens sur un autre
hôte. L'agent reconstruit sa propre échéance à partir de `remaining_ms` (§9).

### 5.5 `observation` (agent → cerveau)

Les champs d'`ActionObservation` : `status` (l'un des `TERMINAL_STATUSES`), `provenance`, `result`
(succès seulement), `error` (`code`, `message`, `retryable` optionnel ; à chaque non-succès),
`parts`. `id` est le `call_id`.

Les `parts` suivent R4 : les parts `text` portent `text` ; les parts `image_ref` portent les sept
`IMAGE_REF_FIELDS` (`attachment_id`, `content_type`, `size`, `width`, `height`, `captured_at`,
`provider_id`) et **doivent nommer une pièce jointe déjà acquittée `accepted: true` dans cette
session, pour cet appel** (§10). Une `observation` portant un `attachment_id` non acquitté — jamais
acquitté, acquitté pour un autre appel, ou dont les octets ne sont plus dans le magasin du cerveau —
est refusée par le cerveau à la frontière, avant que son exécuteur voie la référence : elle devient
une observation `error` pour cet appel (code `attachment_refused` du vocabulaire du cerveau,
`retryable: false` ; statut `external_unknown` au lieu de `error` lorsque l'appel est une `write` que
l'agent n'a pas rapportée `refused`, puisque son effet a pu être engagé), et tout ce que cet appel
avait téléversé est jeté. Une observation ne porte jamais un chemin de fichier, une charge utile ni
une image en ligne (AC33).

### 5.6 `cancel` (cerveau → agent)

Aucun champ au-delà de l'enveloppe ; `id` est le `call_id`. Au mieux : l'agent arrête l'appel s'il le
peut, et la classification de son propre exécuteur (§11) décide de l'observation qu'il retourne, s'il
en retourne une. Le cerveau n'attend jamais de réponse.

### 5.7 `attachment` (agent → cerveau)

| Champ           | Type             | Sens |
| --------------- | ---------------- | ---- |
| `attachment_id` | chaîne non vide  | Identifiant choisi par l'agent, unique dans la session : celui sous lequel un appel vivant tient déjà un téléversement accepté est refusé (§10). |
| `content_type`  | chaîne           | L'un des `ATTACHMENT_CONTENT_TYPES` (`image/png`, `image/jpeg`, `audio/wav` ; addendum de la phase 2, §17). |
| `size`          | entier positif   | Longueur exacte en octets de la trame binaire qui suit. |
| `call_id`       | chaîne, optionnel | Le `call` en vol auquel le téléversement appartient (§10). Absent : l'appel émis le plus récemment et toujours en vol. |

Suivi d'**exactement une trame binaire** de `size` octets (§10).

### 5.8 `attachment_ack` (cerveau → agent)

| Champ           | Type             | Sens |
| --------------- | ---------------- | ---- |
| `attachment_id` | chaîne           | L'identifiant de l'en-tête. |
| `accepted`      | booléen          | `true` lorsque les octets sont stockés sous le bail du run. |
| `code`          | chaîne, seulement quand `accepted: false` | L'un des `ATTACHMENT_ACK_CODES` : `attachment_too_large`, `store_full`, `unexpected_binary`. |

### 5.9 `event` (agent → cerveau)

| Champ        | Type   | Sens |
| ------------ | ------ | ---- |
| `event_type` | chaîne | Doit être dans la liste blanche de l'adaptateur (§15) : `agent.status`. |
| `payload`    | objet  | Validé par l'adaptateur pour ce type. |

Tout `metadata` que porte la trame est jeté et remplacé par l'adaptateur.

### 5.10 `ping` / `pong` (l'une ou l'autre direction)

Aucun champ au-delà de l'enveloppe ; `id` est le `session_id`. Un `pong` répond à un `ping` de même
`id`.

## 6. Limites

| Limite                       | S'applique à                     | Défaut (littéral)           | Fixée par |
| ---------------------------- | -------------------------------- | --------------------------- | --------- |
| `max_frame_bytes`            | toute trame de texte, les deux directions | `1048576` (`PROXY_DEFAULT_MAX_FRAME_BYTES`) | réglage du cerveau, réglage de l'agent ; chaque côté annonce le sien (`hello`, `welcome`). |
| borne binaire                | toute trame binaire              | `min(store max_object_bytes, max_attachment_bytes)` | cerveau : le `max_object_bytes` de son magasin de pièces jointes et le `max_attachment_bytes` du proxy. |
| `max_attachment_bytes`       | une pièce jointe                 | porté dans chaque `call` et dans `welcome.limits` | réglage du cerveau. |
| `max_entries`                | table d'appels de l'agent (§8)   | `1024`                      | réglage de l'agent. |
| `ttl_seconds`                | entrées de la table d'appels de l'agent (§8) | `300`          | réglage de l'agent. |
| `heartbeat_seconds`          | intervalle entre `ping`s (§12)   | `15`                        | les deux côtés. |
| `heartbeat_timeout_seconds`  | attente du `pong` (§12)          | `10`                        | les deux côtés. |

Règles :

- Une trame de texte plus grande que le `max_frame_bytes` du destinataire reçoit
  `error frame_too_large` et la connexion est fermée `4413` (AC32 : une trame de texte de `1048577`
  octets sous les valeurs par défaut). La taille est mesurée sur la trame encodée en UTF-8.
- Une trame binaire plus grande que la borne binaire — ou plus grande que le `size` annoncé par son
  en-tête — reçoit `error frame_too_large` et la connexion est fermée `4413`. La borne est appliquée
  **avant** que les octets soient mis en tampon en entier : le cerveau lit au plus la borne puis
  interrompt.
- Un en-tête dont le `size` dépasse le `max_attachment_bytes` de l'appel courant ou le
  `max_object_bytes` du magasin est refusé par `attachment_ack` avec `accepted: false`, code
  `attachment_too_large`, dès que l'en-tête est lu ; la connexion est conservée et l'en-tête est
  oublié. L'agent ne doit pas envoyer la charge utile d'un en-tête refusé. Refuser un en-tête **ne
  relâche pas** la borne binaire : une trame binaire qui suit malgré tout est traitée exactement
  comme une trame qu'aucun en-tête n'a annoncée — à la borne ou en dessous, elle est jetée sans être
  stockée et refusée par `attachment_ack` `accepted: false` code `unexpected_binary` (connexion
  conservée) ; au-dessus de la borne, c'est `error frame_too_large` avec fermeture `4413`, le
  cerveau lisant au plus la borne avant d'interrompre. Comme un `size` refusé est par définition
  au-dessus de la borne, un agent qui transmet quand même la charge utile déclarée est fermé `4413`.
- Chaque réglage ci-dessus est fini et validé au démarrage ; une valeur omise prend le défaut, une
  valeur invalide fait échouer `--check-config`.

## 7. Codes d'erreur et codes de fermeture

Les valeurs de `code` sont les constantes `PROXY_ERROR_CODES`. « Ferme » est le code de fermeture
WebSocket envoyé immédiatement après la trame `error` ; « conserve » signifie que la connexion reste
ouverte.

| `code`                         | Émis par | `retryable` | Fermeture | Quand |
| ------------------------------ | -------- | ----------- | --------- | ----- |
| `auth_failed`                  | cerveau  | `false`     | `4401`  | Écart de jeton au `hello` (comparaison en temps constant). |
| `agent_limit`                  | cerveau  | `true`      | `4409`  | Un second agent authentifié alors qu'un autre est appairé. |
| `unsupported_protocol_version` | les deux | `false`     | `4400`  | `v` ≠ `1`. |
| `invalid_frame`                | les deux | `false`     | `4400` pour un `hello` mal formé ; conservée sinon | Ce n'est pas un objet JSON, enveloppe mal formée, champ manquant ou de mauvais type, `hello` avec `id` manquant ou vide, `call` avec `seq` manquant ou non entier positif, `call_id` retenu sous un `seq` différent (§8). |
| `frame_too_large`              | les deux | `false`     | `4413`  | Trame de texte au-dessus de `max_frame_bytes`, trame binaire au-dessus de la borne binaire (§6). |
| `unknown_frame`                | les deux | `false`     | conserve | `type` inconnu. |
| `unknown_session`              | cerveau  | `false`     | conserve | Toute trame autre que `hello` avant `welcome` ; une trame de portée session dont l'`id` ≠ le `session_id` courant. |
| `duplicate_call_unknown`       | agent    | `false`     | conserve | `call` avec `seq ≤ last_seq` dont le `call_id` n'est pas retenu (§8). |
| `action_mismatch`              | cerveau  | `false`     | conserve | Une action déclarée diffère de la spécification du catalogue ; cette action est exclue, l'appairage continue. |
| `proxy_disconnected`           | cerveau (en interne, comme observation d'une `read`) | `true` | s.o. | La connexion est tombée pendant que l'appel était en vol (§11). |
| `event_not_allowed`            | cerveau  | `false`     | conserve | `event` avec un `event_type` hors de la liste blanche (§15). |

Codes de fermeture (`PROXY_CLOSE_CODES`), dans la plage applicative 4000–4999 :

| Fermeture | Constante                    | Sens |
| --------- | ---------------------------- | ---- |
| `4400`    | `PROXY_CLOSE_BAD_REQUEST`    | Version de protocole non supportée, ou `hello` invalide. |
| `4401`    | `PROXY_CLOSE_AUTH_FAILED`    | Jeton d'appairage refusé. |
| `4409`    | `PROXY_CLOSE_AGENT_LIMIT`    | Un agent par cerveau est déjà appairé. |
| `4413`    | `PROXY_CLOSE_FRAME_TOO_LARGE`| Trame de texte ou binaire au-dessus de sa borne. |

Un code de fermeture émis par le cerveau ne change pas la politique de reconnexion de l'agent (§13) :
l'agent continue de composer le numéro avec repli tant qu'il tourne. La trame `error` précède la
fermeture pour que l'agent puisse journaliser la raison (assainie, §5.3).

## 8. `seq` et l'identité d'appel bornée de l'agent

Le cerveau attribue `seq` par session : un entier positif, strictement croissant à partir de `1` au
premier `call` après `welcome`. Rien n'est retransmis à la reconnexion (§11) ; un `call` répété sur
la même session ne vient que du cerveau qui renvoie une trame restée sans réponse.

L'agent garde, par session, **un état borné : un entier plus la table d'appels** :

- `last_seq` : le plus grand `seq` accepté ; `0` après `welcome`.
- la **table d'appels** : au plus `max_entries` (défaut `1024`) entrées `(call_id → observation)`
  retenues, chacune évincée après `ttl_seconds` (défaut `300`) sur l'horloge injectée de l'agent, ou
  plus tôt quand la plus ancienne est déplacée par une plus récente.

Les deux sont **réinitialisés à chaque `welcome`**.

| `call` reçu                                           | Règle |
| ----------------------------------------------------- | ----- |
| `seq > last_seq`                                      | Nouveau : exécuté par l'exécuteur de l'agent ; `last_seq` devient son `seq` ; l'observation est retenue sous son `call_id`. |
| `seq ≤ last_seq`, `call_id` retenu                    | Retournée depuis la table **sans exécuter** (0 exécution supplémentaire, AC36). |
| `seq ≤ last_seq`, `call_id` non retenu (évincé ou jamais vu) | Refusé `error duplicate_call_unknown` (`retryable: false`), 0 exécution. |
| `seq` manquant ou non entier positif                  | Refusé `error invalid_frame`. |
| `call_id` retenu sous un `seq` différent              | Refusé `error invalid_frame`. |

Une entrée est retenue dès que l'appel est accepté (ainsi une répétition qui arrive pendant que la
première exécution tourne encore attend la même observation au lieu de lancer une seconde exécution)
jusqu'à son expiration. Une observation retenue retournée à une répétition est identique octet pour
octet à celle envoyée la première fois. Une fois qu'une entrée a été évincée de la table, l'agent ne
peut plus prouver que l'appel n'a pas tourné : il refuse plutôt que de deviner, et la classification
du cerveau pour l'appel d'origine (§11) tient.

## 9. Échéances

Un `call` porte deux champs de temps aux rôles différents :

- `remaining_ms` **borne** l'agent : l'appel est exécuté avec une échéance de
  `min(remaining_ms, le délai propre de l'action côté agent)` mesurée sur l'**horloge monotone de
  l'agent** à partir du moment où la trame est reçue (AC34 : `remaining_ms: 5000` → 5 s même avec une
  horloge murale décalée de 3 h ; `remaining_ms: 20000` avec `action_seconds: 10` → 10 s). Le délai
  propre de l'agent est le délai par action de l'exécuteur (`action_seconds` de son profil, ou le
  `timeout_seconds` de la spécification quand il est plus petit).
- `deadline_utc` est **informatif** : un instant UTC ISO-8601 pour les journaux et les diagnostics.
  Il **n'étend jamais** un budget et ne sert jamais à calculer la borne ; un décalage d'horloge
  murale entre les deux hôtes ne peut donc pas allonger un appel.

Un budget n'est jamais renouvelé d'un saut à l'autre : le cerveau calcule `remaining_ms` depuis sa
propre échéance, et l'agent ne peut que la raccourcir. Quand l'échéance du cerveau expire ou que le
run est annulé, le cerveau envoie `cancel` au mieux (§5.6) et résout l'appel lui-même par la
classification de l'exécuteur (§11) ; une `observation` ultérieure pour cet appel est une observation
tardive (§11).

## 10. Transfert de pièce jointe

Les images voyagent **par référence** : l'agent téléverse les octets dans le magasin de pièces
jointes du cerveau sous le bail du run, puis les nomme depuis une part `image_ref`. Un transfert fait
exactement trois étapes (AC33) :

1. **En-tête** : `attachment` (`attachment_id`, `content_type`, `size`) avec `id` = `session_id`.
2. **Exactement une trame binaire** d'exactement `size` octets. Aucune autre trame ne peut être
   envoyée par l'agent entre l'en-tête et sa trame binaire. Une trame binaire sans en-tête
   précédent, une seconde trame binaire après un en-tête, ou une trame binaire dont la longueur
   diffère de `size`, est refusée par `attachment_ack` `accepted: false` code `unexpected_binary`
   (longueur inférieure à `size`) ou par `error frame_too_large` avec fermeture `4413` (longueur
   au-dessus de la borne binaire, §6).
3. **Acquittement** : `attachment_ack` avec `accepted: true`, ou `accepted: false` et un code :
   - `attachment_too_large` : `size` au-dessus de `max_attachment_bytes` ou au-dessus du
     `max_object_bytes` du magasin. Le refus est envoyé sur le seul en-tête et l'agent ne doit pas
     envoyer la charge utile ; si une trame binaire suit malgré tout, elle est traitée comme non
     annoncée (§6) : jetée et refusée `unexpected_binary` à la borne binaire ou en dessous,
     `error frame_too_large` avec fermeture `4413` au-dessus ;
   - `store_full` : le magasin a refusé l'objet pour une autre borne (`max_objects`,
     `max_total_bytes`, `max_bytes_per_run`) ;
   - `unexpected_binary` : une trame binaire qu'aucun en-tête n'a annoncée (y compris une trame
     envoyée après le refus de son en-tête), un appariement mal formé d'en-tête et de charge utile,
     un en-tête lié à aucun appel en vol (identité d'appel ci-dessous), ou un en-tête dont
     l'`attachment_id` est déjà celui sous lequel un appel vivant tient un téléversement accepté
     (unicité d'identifiant ci-dessous).

Seul un `attachment_id` acquitté peut apparaître dans une part `image_ref` d'une `observation`
ultérieure (§5.5), et seulement dans l'observation de l'appel pour lequel il a été téléversé. Les
octets stockés appartiennent au run nommé par l'appel en vol et suivent le bail et la rétention du
magasin : ils sont libérés avec le nettoyage du run exactement comme les pièces jointes d'un
fournisseur local.

**Acquittement borné.** Le cerveau ne se souvient d'un téléversement accepté — l'`attachment_id` de
l'agent et la référence de magasin vers laquelle il le traduit — que tant que son appel est en vol
et que le magasin détient encore les octets : l'entrée est oubliée avec le résultat terminal de
l'appel (observé, annulé, expiré, répondu par une trame `error`, résolu sur une chute), avec la
libération de l'objet par le run, avec la durée de vie du magasin, et avec la session entière. Au
plus `MAX_ACKNOWLEDGED_ATTACHMENTS` (`256`) entrées de ce genre sont tenues par session ; un
téléversement supplémentaire alors que la table est pleine est refusé `store_full` sur sa charge
utile, rien n'est stocké, et le cerveau compte le refus (`saturated_uploads`). Un agent qui téléverse
et observe appel par appel n'atteint jamais la borne.

**Identité d'appel.** Un téléversement est lié à exactement un appel : celui que nomme le `call_id`
de l'en-tête, sinon l'appel émis le plus récemment et toujours en vol. Un en-tête dont le `call_id`
nomme un appel inconnu ou déjà terminal (observé, annulé, expiré, résolu sur une chute) est refusé
`unexpected_binary` sur le seul en-tête — il n'est jamais imputé à un autre appel en vol. La liaison
est vérifiée à nouveau à l'arrivée de la trame binaire : un appel terminé entre l'en-tête et sa
charge utile invalide l'en-tête, et la charge utile est refusée `unexpected_binary` et jetée, de
sorte qu'un téléversement tardif ne crée jamais de bail après le nettoyage de son run.

**Unicité d'identifiant.** Un `attachment_id` est unique dans la session tant qu'il est acquitté :
un en-tête nommant un identifiant sous lequel un appel encore en vol tient un téléversement accepté —
que cet appel soit un autre ou le même — est refusé `unexpected_binary` sur le seul en-tête, avant
qu'une traduction soit allouée ou remplacée. L'entrée et les octets du premier propriétaire sont
intacts, sa référence acquittée continue de se résoudre dans sa propre observation, et le doublon ne
stocke rien et ne prend aucun bail. Le contrôle est fait contre la table de traduction bornée telle
qu'elle est (aucun historique plus long n'est tenu) : un identifiant dont l'entrée est partie — son
appel terminal, ses octets libérés ou expirés — peut être réutilisé. L'agent transfère avant
d'envoyer l'`observation`, de sorte qu'un transfert refusé lui permet de retourner une observation
`error` (`attachment_refused`) au lieu d'une référence pendante. Les pièces jointes ne sont jamais
retransmises après une chute (§11).

## 11. Chutes de connexion, classification et observations tardives

Quand la connexion tombe (fermeture du transport, expiration du battement de cœur §12, ou arrêt du
cerveau), le cerveau :

- marque **non prête** toute action servie par le proxy jusqu'à ce qu'un nouvel appairage
  `hello`/`welcome` réussisse ; elles sont absentes de la prochaine vue autorisée que le cerveau
  offre au modèle (AC35) ;
- résout tout appel en vol **par la nature de son action**, sans aucune retransmission :
  - `write` → observation `external_unknown`, cause d'erreur `proxy_disconnected` : l'effet a pu
    quitter l'agent et ne doit pas être rejoué ;
  - `read` → observation `error` avec le code `proxy_disconnected`, `retryable: true` : rien
    d'externe n'a eu lieu, le run continue avec cette observation.

La classification de l'exécuteur l'emporte toujours sur une indication de transport : une
`observation` rapportant `success` qui arrive après que le cerveau a déjà résolu l'appel n'est pas
refusionnée.

**Observations tardives.** Une trame `observation` pour un `call_id` déjà terminal (résolu sur une
chute, expiré, annulé, ou déjà observé) ou inconnu du cerveau est **ignorée** — elle ne change rien —
et comptée dans le compteur `late_observations` du proxy, +1 chaque fois (AC36). Une seconde
`observation` pour le même `call_id` est une observation tardive.

**La reconnexion** réappaire de zéro (§4) : nouveau `session_id`, `seq` repartant à `1`, état de
l'agent réinitialisé (§8). Le cerveau ne renvoie jamais les appels qu'il a résolus sur la chute ; un
run qui a besoin de l'action à nouveau émet un nouveau `call` avec un nouveau `call_id`.

## 12. Battement de cœur

Chaque côté envoie `ping` toutes les `heartbeat_seconds` (défaut `15`) avec `id` = `session_id` et
attend un `pong` de même `id` dans `heartbeat_timeout_seconds` (défaut `10`). Aucun `pong` à temps
est une **chute** : le côté qui l'a constatée ferme la connexion et applique le §11 (cerveau) ou le
§13 (agent). Un `pong` dont l'`id` n'est pas le `session_id` courant ne compte pas (§3). Les
battements de cœur tournent sur des horloges injectées dans les tests ; aucun sommeil de durée
positive n'est utilisé pour les observer.

## 13. Repli de reconnexion (décision 9)

L'agent compose `brain_url` tant qu'il tourne, avec un nombre illimité de tentatives :

- le délai avant la tentative *n* (n ≥ 0 en comptant les échecs consécutifs) est un tirage en
  **gigue complète** : `rng() × min(cap, initial × multiplier^n)` avec `initial` = `1` s,
  `multiplier` = `2`, `cap` = `30` s, `rng()` uniforme dans [0, 1) depuis le **générateur injecté** ;
- un `welcome` **réinitialise** le compteur d'échecs, de sorte que l'échec suivant repart de la base
  de 1 s ;
- un appairage refusé (`4401`, `4409`, `4400`, `4413`) compte comme un échec, comme une erreur de
  transport.

Avec un générateur qui retourne 0.5, les délais après six échecs consécutifs sont exactement
`[0.5, 1.0, 2.0, 4.0, 8.0, 15.0]` s (AC37). Le dormeur est injecté ; les tests ne dorment jamais.

## 14. TLS et règles de validation

- Le `listen.host` du cerveau ne peut être hors bouclage **qu'avec `tls` configuré** (références de
  certificat et de clé, style `${NAME}`, aucun secret littéral) ; une écoute hors bouclage sans
  `tls` est **refusée à la validation** (`--check-config` sort en 2, en nommant le module et le
  champ, jamais une valeur).
- Le `brain_url` de l'agent ne peut être hors bouclage **qu'avec le schéma `wss`** ; une URL `ws://`
  hors bouclage est refusée à la validation de la même façon.
- Le bouclage (`127.0.0.1`, `::1`, `localhost`) peut utiliser le `ws` simple — l'essai à deux
  processus (AC39) tourne sur le bouclage.
- Le jeton d'appairage est un justificatif d'identité : référencé comme `${NAME}`, comparé en temps
  constant, masqué dans les traces et les diagnostics de perte comme tout secret configuré. Il
  n'apparaît jamais dans un `message` d'`error`.

## 15. Liste blanche de l'adaptateur d'événements

Les trames `event` n'atteignent le bus local du cerveau qu'à travers l'**adaptateur de source** du
proxy, qui :

- n'accepte que `event_type` ∈ {`agent.status`} ; tout autre type — y compris les types de trace de
  supervision (`module.*`, `input.*`, `brain.*`, `action.*`) et tout type `channel.*` — reçoit
  `error event_not_allowed` et ne publie rien (AC38) ;
- valide le `payload` pour le type accepté ;
- pose lui-même `metadata.source = "proxy"` et `metadata.provider_id = agent_id` (la valeur du
  `hello`), quel que soit le metadata porté par la trame ;
- publie l'événement résultant sur le bus local.

Aucun appel `subscribe`, aucun événement de bus et aucune trace ne traverse jamais le transport dans
un sens ou dans l'autre : le fil porte les douze types de trame du §5 et rien d'autre.

## 16. Récapitulatif de qui applique quoi

| Règle                                           | Cerveau (`modules/proxy`) | Agent (`modules/agent_link`) |
| ----------------------------------------------- | :-----------------------: | :--------------------------: |
| Enveloppe, `v`, `type` inconnu                  | ✔                         | ✔ |
| Jeton, temps constant, `4401`                   | ✔                         |   |
| Un agent, `4409`                                | ✔                         |   |
| Comparaison de spécification, `action_mismatch` | ✔                         |   |
| `max_frame_bytes`, `4413`                       | ✔                         | ✔ |
| Borne binaire `min(max_object_bytes, max_attachment_bytes)` | ✔             |   |
| `seq`, `last_seq`, table d'appels, `duplicate_call_unknown` |                | ✔ |
| Borne `remaining_ms` sur une horloge monotone   |                           | ✔ |
| Exécuteur en refus par défaut pour un `call` transmis |                     | ✔ |
| Classification des chutes par nature, `late_observations` | ✔                  |   |
| Battement de cœur                               | ✔                         | ✔ |
| Repli, tentatives illimitées                    |                           | ✔ |
| TLS à la validation                             | ✔ (`listen`/`tls`)        | ✔ (`wss`) |
| Liste blanche d'événements, réécriture du `metadata` | ✔                    |   |

## 17. Addendum de la phase 2

La phase 2 (`docs/campaigns/phase2/spec.md`, R5 et décision 11) étend ce que le protocole v1
transporte sans changer sa version ni ses règles :

- **Types de contenu.** `attachment.content_type ∈ {image/png, image/jpeg, audio/wav}` — l'ensemble
  partagé `ATTACHMENT_CONTENT_TYPES` de `core/contracts.py`, que le magasin du cerveau, le proxy et
  l'agent utilisent de la même façon. Toute autre valeur (`audio/mpeg`, par exemple) donne `error
  invalid_frame` et rien n'est stocké. Le magasin du cerveau enregistre le type de l'en-tête
  `attachment` acquitté, jamais le type qu'une part répète.
- **Parts `audio_ref`.** Une `observation` peut porter des parts `audio_ref` (les
  `AUDIO_REF_FIELDS`, `transcription` incluse) sous exactement la règle de référence acquittée des
  §5.5 et §10 : l'agent transfère la pièce jointe de chaque `audio_ref` avant d'envoyer
  l'observation et retourne `error attachment_refused` — jamais une référence pendante — quand un
  transfert est refusé ; le cerveau refuse un `audio_ref` nommant un `attachment_id` non acquitté
  avec `error attachment_refused` (`retryable: false` ; `external_unknown` pour une `write` que
  l'agent n'a pas rapportée `refused`) et jette les téléversements de l'appel. Le cerveau ne
  réécrit que `attachment_id` ; tous les autres champs de la part, `transcription` incluse,
  atteignent l'exécuteur tels quels.
- **Inchangé.** `v` reste `1`. Les douze types de trame du §5, les limites du §6 (borne binaire
  `min(max_object_bytes, max_attachment_bytes)`, défaut 1 048 576 octets), les codes d'erreur,
  d'`attachment_ack` et de fermeture du §7, le transfert en trois étapes, l'identité d'appel,
  l'unicité de l'`attachment_id` et la règle de non-retransmission du §11 sont par ailleurs
  inchangés. Une écriture transmise — `audio.speak`, `audio.play`, `stream.scene.set` — tombée après
  sa trame `call` se résout en `external_unknown` (cause `proxy_disconnected`) comme toute écriture.
- **Limite de disponibilité distante (décision 11).** La vue prête du cerveau liste une action liée
  au proxy comme prête tant qu'un agent l'ayant déclarée est appairé ; une action dont le module
  n'est pas prêt chez l'agent répond `refused provider_not_ready` depuis l'exécuteur de l'agent au
  moment de l'appel. Les changements de disponibilité côté agent ne sont **pas** propagés sur le fil
  dans le protocole v1 ; c'est une limite enregistrée, renvoyée à une révision ultérieure du
  protocole.

## 18. Addendum de la phase 3

La phase 3 (`docs/campaigns/phase3/spec.md`, R5 ; correctif de porte P27F3) ajoute une clé
optionnelle et additive à une déclaration d'`actions` du `hello` (§5.1) sans changer la version ni
aucune règle :

- **`model_proposable`.** Une action dont la spécification est proposable au modèle (seule
  `moderation.request` l'est aujourd'hui) se déclare avec `model_proposable: true` ; la clé est
  absente sinon, de sorte que toute déclaration de la phase 2 est inchangée. Le cerveau reconstruit
  une clé absente en `false`. Le drapeau fait partie de la spécification comparée (§5 étape 4) : un
  fournisseur distant d'une action proposable ne s'appaire que s'il déclare le drapeau, et une
  déclaration qui l'omet — un agent plus ancien — donne `error action_mismatch` pour cette seule
  action, jamais un appairage silencieux d'une spécification que le cerveau offrirait autrement au
  modèle. Les refus du contrat (jamais sur une action `read`, jamais avec `delivery`) s'appliquent à
  la spécification reconstruite, donc une telle déclaration est aussi un écart.
- **Inchangé.** `v` reste `1` ; les types de trame, les limites, les codes d'erreur et de fermeture
  sont ceux des §5–§17.
