# Pont d'automatisation — guide opérateur

[English](automation-bridge.md) · **Français**

> Ce document est la traduction de [`docs/automation-bridge.md`](automation-bridge.md) : **le texte
> anglais fait foi** là où les deux diffèrent.

Ce guide est la référence du pont d'automatisation, le module optionnel `streamerbot`.

**Statut du pont d'automatisation : not delivered — awaiting real-instance trial** (non
livré, en attente de l'essai sur une instance réelle). Tout ce qui suit est éprouvé contre une fausse instance qui
rejoue des trames documentées, jamais contre une vraie. La section
[Statut de l'essai](#statut-de-lessai) dit ce qui reste à vérifier.

Le pont relie le compagnon à une application d'automatisation de stream que vous utilisez déjà —
une instance Streamer.bot, appelée *l'instance* ci-dessous. Il est écrit à partir de l'étude
`docs/research/streamerbot-etude-2026-10.md` et des décisions du brief de la phase 5.

## Ce que fait le pont, et ce qu'il ne fait pas

Le pont est **optionnel dans tous les cas**. Non activé, le compagnon est exactement ce qu'il est
sans lui : aucun réglage ne devient requis, aucun défaut ne change, et aucun profil livré ne
l'active ni ne le nomme. Activé mais incapable de joindre l'instance, le compagnon atteint quand
même l'état prêt ; le pont se déclare dégradé et ses capacités répondent « pas prêt ».

Ce qu'il fait, sur **une seule** connexion WebSocket authentifiée que le compagnon ouvre vers le
serveur WebSocket de l'instance elle-même (le compagnon est le client ; il n'ouvre aucun port
d'écoute) :

- **Un puits.** Une action d'écriture, `automation.action.run`, exécute une action nommée de
  votre instance, derrière les règles d'autorisation du compagnon, une liste blanche, des
  échéances et des résultats honnêtes.
- **Des déclencheurs (cues).** Un événement que votre instance achemine délibérément vers le
  compagnon — un *cue* — entre comme un événement de chat ordinaire de genre `cue`, évalué par les
  politiques de déclenchement sous `triggers.streamerbot`, et peut lancer un run du brain.
- **Des entrées remplacées, jamais parallèles.** Quand le profil le dit, l'instance *remplace*
  l'entrée de chat d'une plateforme (la famille `chat`) ou la lecture des spectateurs (la famille
  `viewers`). Pour chaque paire (famille, plateforme), au plus un module est actif ; le module
  natif cesse d'ingérer cette paire.

Ce qu'il ne fait **pas** :

- Il ne rapporte jamais qu'une action de l'instance s'est exécutée. L'instance accuse réception
  d'une requête ; elle ne confirme pas l'exécution, donc un run se termine `external_unknown`,
  jamais `success`.
- Il n'envoie pas dans le chat. Les réponses restent aux modules de plateforme (`twitch`, `kick`,
  `youtube`). Une plateforme dont l'instance lit le chat et dont le module de plateforme est
  désactivé n'a aucun fournisseur `chat.write` : une réponse y finit `no_provider`.
- Il n'attend pas de confirmation aller-retour de vos actions (question 6 de l'étude).
- Il ne découvre pas les actions à votre place : seul ce que la liste blanche nomme est joignable.
- Il n'a pas de repli. Si l'instance est hors service, rien d'autre ne reprend les paires qu'il
  possède.
- Il ne change pas le protocole du proxy : placé dans un processus agent, seule l'action de run
  atteint le brain (combinaison 8).

## Réglages

Chaque réglage vit sous `modules.streamerbot`. Rien n'est requis dans le schéma ; le hook de
réglages refuse, à `--check-config`, une configuration que le pont ne peut pas utiliser, avec un
diagnostic par champ qui ne reprend jamais une valeur.

| Réglage | Défaut | Signification |
| --- | --- | --- |
| `url` | aucun (refusé s'il manque) | Le serveur WebSocket de l'instance, `ws://` ou `wss://`. Un hôte qui n'est pas en boucle locale doit utiliser `wss` ; `ws` simple n'est accepté que sur une adresse de boucle locale. Aucune adresse n'est supposée. |
| `password` | aucun | Le mot de passe d'authentification du serveur, toujours en référence `${NAME}`. Un identifiant déclaré : masqué dans toute trace, tout journal et tout diagnostic. Absent, le pont ne s'authentifie pas, et il est dégradé `authentication_required` si l'instance l'exige. |
| `companion_name` | aucun (refusé s'il manque) | Le nom par lequel les spectateurs s'adressent au compagnon ; la politique de déclenchement par défaut accepte un message de chat qui le mentionne. |
| `deadlines.connect_seconds` | `5` | Durée maximale de l'ouverture de la connexion. |
| `deadlines.authentication_seconds` | `5` | Durée maximale du hello et de l'échange d'authentification. |
| `deadlines.request_seconds` | `5` | Durée maximale d'attente de la réponse à une requête (inventaire, abonnement, liste des spectateurs). |
| `deadlines.acknowledgement_seconds` | `10` | Durée maximale d'attente de l'accusé de réception de l'instance pour un run. Elle doit rester au moins 1 seconde sous l'échéance propre de 15 secondes de l'action de run, pour que la cause du pont ne soit jamais devancée. |
| `reconnect.min_delay_seconds` | `1` | Le premier délai après une connexion perdue. |
| `reconnect.max_delay_seconds` | `60` | Le plafond sous lequel reste chaque délai de reconnexion. |
| `queue_max_events` | `256` | Combien d'événements entrants attendent le consommateur. Un événement qui arrive sur une file pleine est abandonné et compté. |
| `allowlist` | vide | Les actions de l'instance que l'action de run peut atteindre (section suivante). Vide ou absente, chaque run est refusé. |
| `companion_identities` | vide | L'identifiant du compte du compagnon par plateforme, par exemple `{twitch: ${TWITCH_BOT_USER_ID}}`. Requis pour chaque plateforme dont le pont possède le chat (anti-écho). |
| `instance_identity` | vide | L'identifiant du compte du diffuseur par plateforme : la chaîne à laquelle est attribué un cue qui n'en porte pas, et la seule chaîne pour laquelle répond la lecture des spectateurs du pont. |
| `trust_instance_roles` | `false` | Si les rôles rapportés par l'instance (modérateur, abonné…) satisfont les règles `audience`. |

Chaque échéance et chaque délai est un nombre fini et positif de secondes ; `queue_max_events`
est un entier positif ; `reconnect.min_delay_seconds` ne doit pas dépasser
`reconnect.max_delay_seconds`. Une identité ne doit pas contenir `:`. Le bloc réservé `limits`
que reçoit chaque module est accepté et n'est pas lu.

## La liste blanche

L'instance n'autorise personne : quiconque peut parler à son serveur peut exécuter n'importe
laquelle de ses actions. La liste blanche est la restriction propre du compagnon par-dessus.

- Chaque entrée nomme **exactement une** action de l'instance, `{action: <nom>}`, ou **un** groupe
  de l'instance, `{group: <nom>}`, tels que l'instance les affiche.
- Une entrée peut porter un schéma `arguments` (le même sous-ensemble de schéma que les réglages
  des modules). Une entrée sans schéma n'accepte qu'un objet `arguments` vide.
- Deux entrées `action:` du même nom, ou deux entrées `group:` du même nom, sont refusées par le
  hook de réglages.
- Quand plusieurs entrées correspondent à une même action de l'instance, seul le schéma de son
  entrée `action:` décide si une entrée la nomme ; sinon les arguments doivent satisfaire le
  schéma de chaque entrée `group:` correspondante.
- **Une entrée de groupe délègue le choix des actions joignables à quiconque modifie ce groupe
  dans l'instance.** Quiconque peut ajouter une action à ce groupe dans l'instance la rend
  joignable par le compagnon, sans aucun changement de votre profil. Préférez les entrées
  `action:` ; n'utilisez un groupe que pour un groupe que vous êtes seul à modifier.
- Les règles d'autorisation du compagnon s'appliquent toujours en premier, inchangées : un run
  exige une règle `actions:` accordant `automation.run` sur la portée `automation`, sinon il est
  refusé `not_authorized` avant que le pont ne le voie.
- À chaque connexion réussie, le pont lit l'inventaire de l'instance et y réconcilie la liste
  blanche. L'ensemble joignable est chaque nom d'action configuré, plus chaque action d'un groupe
  configuré, qui existe exactement une fois dans l'inventaire et qui est activé. Une trace sans
  valeur, `automation.allowlist.reconciled`, liste les entrées qui n'atteignent rien, par leur nom
  configuré. Un inventaire illisible laisse le pont pas prêt (`inventory_unavailable`).

Chaque refus de la liste blanche, et chaque violation des arguments, est décidé avant qu'aucune
trame ne quitte le processus.

```yaml
modules:
  streamerbot:
    allowlist:
      - action: BRB
      - action: Shoutout
        arguments:
          type: object
          properties:
            user:
              type: string
          additionalProperties: false
```

## Propriété des entrées — sans repli

Trois familles d'entrées existent, définies par le noyau et ne nommant aucun éditeur :

- `chat` — les messages de chat et les notifications de plateforme (abonnements, raids, follows,
  dons…) d'une plateforme ;
- `viewers` — la lecture des spectateurs `users.read` d'une plateforme ;
- `cues` — les événements qu'une automatisation de l'opérateur achemine délibérément vers le
  compagnon.

Un module déclare, dans son manifest, quelles paires (famille, plateforme) il peut servir et s'il
les sert par défaut. Le pont déclare `chat` et `viewers` pour `twitch`, `kick` et `youtube` **non**
par défaut, et `cues` sur toutes les plateformes (`*`) par défaut. Ainsi, activé sans bloc
`inputs`, le pont ne possède que les cues, et les modules natifs gardent leur chat et leurs
spectateurs.

Le bloc optionnel de premier niveau `inputs` décide une paire explicitement : famille →
plateforme (ou `'*'`) → propriétaire, où le propriétaire est un nom de module activé ou le
littéral `none` (délibérément sans propriétaire).

```yaml
inputs:
  chat:
    twitch: streamerbot     # l'instance lit le chat twitch ; le module twitch cesse de l'ingérer
  cues:
    '*': none               # aucun cue n'atteint le compagnon
```

Résolution, pour une paire : l'entrée explicite `inputs.F.P` ; sinon `inputs.F.'*'` ; sinon le
seul module activé qui déclare la paire par défaut ; sinon la paire n'a pas de propriétaire. Deux
déclarants par défaut, une liste de deux modules propriétaires, un propriétaire non activé ou qui
ne déclare pas la paire, et une famille hors du vocabulaire sont chacun refusés à la préparation
— par `run` comme par `--check-config`, avant qu'aucun module ne soit préparé et avant qu'aucune
socket ne s'ouvre — avec un diagnostic nommant la famille, la plateforme et les fournisseurs ou
la valeur.

À l'exécution, un événement qu'un module publie pour une paire qu'il ne possède pas n'atteint
aucun abonné, est compté dans `input_ownership_rejections`, et est diagnostiqué une fois par
(module, famille, plateforme) par la trace `input.ownership.rejected`. Un module de plateforme qui
ne possède pas sa paire de chat n'ouvre aucune ingestion pour elle mais garde `chat.write` et son
état prêt.

**Il n'y a pas de repli.** Quand le pont possède une paire et que l'instance est hors service,
rien n'est ingéré pour cette paire et rien ne la reprend : l'enregistrement `module.degraded` du
pont en est la conséquence visible. Pour revenir à une entrée native, changez le profil.

L'interface de configuration montre la table résolue en lecture seule sur la page des réglages du
cœur — propriétaire, origine (entrée explicite ou règle par défaut) et chaque candidat — et son
Check rapporte les diagnostics de propriété. La propriété ne se change que dans le profil.

## Résultats d'un run

L'action de run `automation.action.run` prend `{action: <nom>, arguments: {…}}` : `action` est
requis (1 à 128 caractères), `arguments` est optionnel et aucune autre clé n'est acceptée. C'est
un `write` avec `idempotency: none`, la permission `automation.run`, une destination joker de
portée `automation`, non proposable par le modèle, et une livraison d'effet seul
(`text_argument: none`), si bien qu'une route peut l'utiliser comme livraison fixe.

Ses résultats sont exactement ceux-ci. Aucun ne dit que l'action de l'instance s'est exécutée,
parce que l'instance ne le dit jamais ; chacun porte `retryable: false`, et le run n'est jamais
retenté.

| Situation | Statut | Code | Cause | Ce que sait le compagnon |
| --- | --- | --- | --- | --- |
| Le nom ou le groupe n'est pas dans la liste blanche | `refused` | `not_allowlisted` | — | Rien n'a été envoyé. |
| Dans la liste blanche, absente de l'inventaire de l'instance | `refused` | `instance_action_missing` | — | Rien n'a été envoyé. |
| Nom de la liste blanche correspondant à deux actions de l'instance ou plus | `refused` | `instance_action_ambiguous` | — | Rien n'a été envoyé. |
| L'action de l'instance est désactivée dans l'inventaire | `refused` | `instance_action_disabled` | — | Rien n'a été envoyé. |
| `arguments` viole le schéma de l'entrée | `error` | `invalid_arguments` | — | Rien n'a été envoyé. |
| Le pont n'est pas prêt | `refused` | `provider_not_ready` | — | Rien n'a été envoyé. |
| L'instance a accusé réception de la requête | `external_unknown` | `external_effect_unknown` | `execution_unconfirmed` | La requête a été acceptée ; on ne sait pas si l'action s'est exécutée. |
| L'instance a répondu une erreur | `error` | `provider_failed` | `instance_rejected` | L'instance a refusé la requête. |
| Requête envoyée, puis aucun accusé de réception avant l'échéance, ou connexion perdue, ou appel annulé | `external_unknown` | `external_effect_unknown` | `acknowledgement_lost` | La requête a pu atteindre l'instance ; on ne sait pas si l'action s'est exécutée. |
| Échéance ou annulation avant que la trame ne quitte le processus | `timeout` / `cancelled` | codes existants | — | Rien n'a été envoyé. |

Le modèle et l'opérateur voient tous deux ces résultats : l'observation revient dans la boucle du
brain et le résultat est dans la trace. Une requête est envoyée au plus une fois par appel.

## Connexion, état prêt et événements perdus

- **État prêt.** Le pont n'est prêt que tant qu'il est connecté, authentifié (avec le schéma
  documenté de sel et de défi de l'instance, quand l'instance l'exige) et qu'il détient un
  inventaire réconcilié. Avant cela, l'action de run et le `users.read` du pont sont refusés
  `provider_not_ready` sans rien envoyer.
- **Le démarrage n'échoue jamais à cause de l'instance.** Une instance injoignable, un mot de
  passe refusé, un mot de passe manquant que l'instance exige, un hello malformé ou un inventaire
  illisible donnent un compagnon prêt et un enregistrement `module.degraded` pour le pont, avec la
  raison sans valeur `unreachable`, `authentication_failed`, `authentication_required`,
  `protocol_error` ou `inventory_unavailable`. Aucun mot de passe, aucune URL ni aucun texte de
  trame n'apparaît dans une trace ou un journal.
- **Une connexion perdue** rend le pont pas prêt immédiatement (un `module.degraded`). Il se
  reconnecte avec un backoff borné, de `reconnect.min_delay_seconds` jusqu'à
  `reconnect.max_delay_seconds` ; une reconnexion réussie rapporte de nouveau `module.ready` —
  jamais en silence.
- **Les événements émis pendant la déconnexion sont perdus.** L'instance ne rejoue pas ce qu'elle
  a publié pendant que le pont n'était pas connecté : un message de chat, une notification ou un
  cue envoyé pendant une coupure n'atteint jamais le compagnon. Rien n'est reconstitué après la
  reconnexion.
- **Abonnements.** Le pont ne s'abonne qu'aux catégories d'événements dont ont besoin les paires
  qu'il possède : la catégorie des événements personnalisés pour les cues, et, pour chaque
  (`chat`, P) possédé, les catégories message de chat et notification de P. Un événement de
  l'instance sans correspondance documentée est abandonné et compté.
- **File bornée.** Les événements entrants attendent dans une file de `queue_max_events` ; un
  événement qui arrive sur une file pleine est abandonné et compté (`dropped_events`), sans
  jamais bloquer la connexion.
- **L'arrêt** ferme la connexion dans le budget de fermeture du compagnon ; un run en cours se
  termine avec le résultat de son état dans la table ci-dessus.

## Les cues — la recette

Un cue est la façon dont votre instance dit « réagis à ceci maintenant » : une récompense de
points de chaîne, un raccourci clavier, un minuteur, n'importe quel déclencheur de l'instance.
Dans l'instance, terminez l'action qui doit atteindre le compagnon par la sous-action *Trigger
Custom Event* avec *Use Args* coché, et définissez avant elle ces arguments :

| Argument | Requis | Règle |
| --- | --- | --- |
| `cue_id` | oui | Non vide, 128 caractères au plus, nouveau pour chaque cue : un identifiant répété dans la fenêtre de dédoublonnage est traité comme le même cue. |
| `platform` | oui | La plateforme que concerne le cue, par exemple `twitch`. |
| `channel_id` | non | Par défaut `instance_identity` de cette plateforme ; sans l'un ni l'autre, le cue est abandonné. |
| `user_id`, `user_name` | non | Le spectateur que concerne le cue. Absent, l'auteur est une identité stable de l'instance, distincte de tout spectateur et de toute identité du compagnon. |
| `text` | oui | Une chaîne, vide permise, 500 points de code au plus ; ce que la `command` d'une route compare. Absent ou d'un autre type, le cue est abandonné. |

Le pont le transforme en un `channel.chat.message` avec `payload.kind: cue` et
`payload.message_id` égal à `cue_id`. Une enveloppe malformée ou trop grande est abandonnée et
comptée, jamais tronquée ni réparée.

La politique par défaut sous `triggers.streamerbot` accepte chaque cue. Une route du brain décide
ensuite de ce qui se passe ; une route qui ne correspond qu'aux cues peut clore le run par
l'action de run, comme une route sur les notifications de plateforme :

```yaml
modules:
  brain:
    routes:
      - name: brb
        match:
          kinds: [cue]
          command: "!brb"
        instructions: "The streamer steps away: say so in one short sentence."
        delivery:
          mode: fixed
          actions:
            - action: automation.action.run
              text_argument: none
              arguments:
                action: BRB
```

Un cue qu'aucune route ne reconnaît est traité comme tout événement de chat accepté, avec les
instructions et la livraison par défaut du brain. Un cue n'est pas enregistré dans le contexte de
chat, `users` n'enregistre jamais son auteur, et `moderation` et `viewer_memory` n'agissent que sur
les messages ordinaires.

## Modèle de confiance

- **L'instance est la vôtre.** Le pont fait confiance à l'instance parce qu'elle appartient à
  l'opérateur et qu'elle est jointe par une connexion authentifiée (l'hypothèse des cues, A5).
  C'est pourquoi une route qui ne correspond qu'aux cues peut porter une livraison d'effet, comme
  une route sur les notifications de plateforme, sans règle `audience`.
- **Quiconque peut modifier les actions de l'instance peut déclencher ces routes.** Une route de
  cue exécute ce que dit sa livraison quand arrive un cue portant sa commande ; quiconque peut
  ajouter un *Trigger Custom Event* à une action de l'instance — un co-modérateur qui y a accès,
  un paquet d'actions importé — peut la lancer. Traitez l'accès en modification à l'instance comme
  un accès à chaque route de cue.
- **Quiconque peut modifier un groupe de la liste blanche choisit ce qu'atteint l'action de run**
  (voir la liste blanche).
- **Le texte n'accorde jamais de rôle.** Le texte d'un cue est comparé par `command`, jamais lu
  comme une revendication de rôle. Les rôles des événements de chat traduits ne comptent qu'avec
  `trust_instance_roles: true`, et leur provenance nomme le pont.
- **Anti-écho.** Les identités du compagnon sont remises au moteur de déclenchement, qui refuse ce
  que le compagnon a écrit. Une identité erronée — l'identifiant d'un spectateur au lieu de celui
  du compagnon — ferait taire ce spectateur : vérifiez `companion_identities` avec les identifiants
  de la plateforme elle-même.
- **Le mot de passe** est un identifiant déclaré, référencé en `${NAME}` ; il n'atteint jamais une
  réponse, une ligne de journal ni un diagnostic. Utilisez `wss://` pour toute instance qui n'est
  pas sur la machine du compagnon.

## Statut de l'essai

Non livré — en attente de l'essai sur une instance réelle. Les tests contre la fausse instance
rejouent des trames dont la documentation de l'instance donne la forme ; aucun test n'affirme un
comportement de l'instance que l'étude liste comme non vérifié. Huit nœuds d'essai opt-in,
`tests/test_phase5_trials.py`, un par question de l'étude, sont sautés tant que leurs variables ne
sont pas définies :

1. la réponse de l'instance à une requête nommant une action inexistante ;
2. la latence, et si sa file de requêtes bloque ;
3. l'authentification en mode imposé ;
4. la version minimale par requête ;
5. la disponibilité de HTTP/JSON en C# (une observation de l'opérateur) ;
6. l'aller-retour des événements personnalisés ;
7. les serveurs sous Wine (une observation de l'opérateur) ;
8. le volume des événements abonnés.

Aucun refus du pont ne dépend de la réponse à la question 1. Tant que l'enregistrement d'essai
`docs/campaigns/phase5/bridge-trial-record.md` n'existe pas et ne répond pas aux huit questions
avec la version de l'instance et la date, ce guide, le README et l'index de la documentation
donnent au pont le statut not delivered.

## Combinaisons

Chaque installation se fait par la seule configuration. Chaque combinaison ci-dessous donne le
fragment à appliquer au profil livré nommé, et ce qui est activé, désactivé et refusé. Les lignes
9 à 12 sont refusées par `--check-config` ; leur diagnostic est cité sous le fragment.

**Appliquer un fragment.** Un mapping se fusionne clé par clé dans le mapping du profil ; une
liste prolonge la liste du profil de même clé (ses éléments sont ajoutés après ceux qui existent)
; toute autre valeur remplace celle du profil. Exportez ensuite chaque `${NAME}` que le résultat
référence et lancez `python -m core.main --config <profil> --check-config`. Un fragment n'est pas
un overlay tel quel : un overlay remplace une liste en entier, il doit donc porter les listes
complètes.

| # | Installation | Activé | Désactivé | Refusé |
| --- | --- | --- | --- | --- |
| 1 | Instance absente : le pont non activé (`config.yaml.example`) | Tout ce qu'active le profil livré, inchangé | Le pont : aucun réglage, aucune socket, aucun bloc `inputs` | Un appel à `automation.action.run` : aucun module activé ne le déclare (`unknown_action`) |
| 2 | Même machine, puits seul (`config.yaml.example`) | L'action de run du pont, accordée au brain ; chat et spectateurs natifs inchangés | Les cues : `inputs.cues.'*'` vaut `none` | Un run hors de la liste blanche (`not_allowlisted`) ; chaque cue (sans propriétaire) |
| 3 | Même machine, puits et cues (`config.yaml.example`) | L'action de run ; les cues sur toutes les plateformes, évalués sous `triggers.streamerbot` ; la route de cue `brb` | Rien de natif : le chat twitch et `users` gardent leurs paires | Un run hors de la liste blanche ; une route sur `message` portant l'action de run sans `audience` (refusée à la préparation) |
| 4 | Même machine, l'instance lit le chat Twitch (`config.yaml.example`) | Chat et notifications Twitch par l'instance, évalués sous `triggers.streamerbot` ; `chat.write` reste au module `twitch` | L'ingestion de chat du module `twitch` (aucun abonnement EventSub) | Un événement de chat twitch publié par le module `twitch` (abandonné et compté) ; toute reprise pendant que l'instance est hors service |
| 5 | Plusieurs plateformes : l'instance possède le chat Kick, le module natif `twitch` le chat Twitch (`config.yaml.example`) | Le chat Kick par l'instance ; le chat Twitch par le module `twitch` | Une entrée Kick native (le module `kick` reste désactivé) | Une réponse sur Kick : aucun fournisseur `chat.write` là (`no_provider`) ; un événement Kick venant de tout autre module |
| 6 | L'instance possède `viewers` pour Twitch (`config.server.yaml.example`) | `users.read` sur Twitch répondu depuis les spectateurs actifs de l'instance (`active_viewers`, `complete: false`) ; `users` sert les autres plateformes | La liaison du module `users` pour Twitch | `users.read` sur Twitch pour toute chaîne autre que `instance_identity.twitch` (`unsupported_destination`) |
| 7 | Brain distant : le pont sur l'hôte du brain en `wss://` (`config.server.yaml.example`) | Le pont qui joint l'instance par une façade TLS (par exemple la recette Tailscale Serve de l'instance) ; l'action de run et les cues sur l'hôte du brain | `ws://` simple vers l'hôte de l'instance | Une URL `ws://` simple vers cet hôte (combinaison 11) ; un hôte injoignable n'est pas un refus : un compagnon prêt, le pont dégradé `unreachable` |
| 8 | Le pont dans le processus agent, puits seul, joint par la liste d'actions du proxy (`agent.yaml.example`) | L'action de run, prêtée au brain par `agent_link` et servie sur l'hôte du brain par la liste d'actions du module `proxy` | Les familles et les cues n'atteignent pas le brain dans ce placement : le protocole proxy v1 ne transporte que l'événement `agent.status`, donc `inputs.cues.'*'` vaut `none` | Un run que les règles de l'hôte du brain ou de l'agent n'accordent pas (`not_authorized`) ; un run hors de la liste blanche (`not_allowlisted`) |
| 9 | Refusé : deux propriétaires pour une paire (`config.yaml.example`) | Rien : le profil est refusé | Chaque module : aucun n'est préparé | `inputs.chat.twitch` nommant deux modules |
| 10 | Refusé : un propriétaire non activé (`config.yaml.example`) | Rien : le profil est refusé | Chaque module : aucun n'est préparé | `inputs.chat.twitch: streamerbot` sans le pont dans `enabled_modules` |
| 11 | Refusé : une URL `ws://` hors boucle locale (`config.yaml.example`) | Rien : le profil est refusé | Chaque module : aucun n'est préparé | `url` en `ws` simple vers un hôte hors boucle locale |
| 12 | Refusé : le pont possédant une paire de chat sans identité du compagnon pour elle (`config.yaml.example`) | Rien : le profil est refusé | Chaque module : aucun n'est préparé | (`chat`, twitch) possédé par le pont sans `companion_identities.twitch` |

### Combinaison 1 — instance absente

Le pont n'est pas activé : il n'y a rien à ajouter.

<!-- combination 1 base=config.yaml.example -->
```yaml
{}
```

### Combinaison 2 — même machine, puits seul

<!-- combination 2 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    allowlist:
      - action: BRB
inputs:
  cues:
    '*': none
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combinaison 3 — même machine, puits et cues

<!-- combination 3 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
    allowlist:
      - action: BRB
  brain:
    routes:
      - name: brb
        match:
          kinds: [cue]
          command: "!brb"
        instructions: "The streamer steps away: say so in one short sentence."
        delivery:
          mode: fixed
          actions:
            - action: automation.action.run
              text_argument: none
              arguments:
                action: BRB
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combinaison 4 — l'instance lit le chat Twitch

Le module `twitch` reste activé pour `chat.write` ; son ingestion de chat s'arrête.

<!-- combination 4 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    companion_identities:
      twitch: ${TWITCH_BOT_USER_ID}
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
inputs:
  chat:
    twitch: streamerbot
```

### Combinaison 5 — plusieurs plateformes

L'instance lit le chat Kick ; le module natif `twitch` garde le chat Twitch par sa règle par
défaut.

<!-- combination 5 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    companion_identities:
      kick: ${KICK_BOT_USER_ID}
    instance_identity:
      kick: ${KICK_CHANNEL_ID}
inputs:
  chat:
    kick: streamerbot
```

### Combinaison 6 — l'instance possède les spectateurs Twitch

<!-- combination 6 base=config.server.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
inputs:
  viewers:
    twitch: streamerbot
```

### Combinaison 7 — brain distant en `wss://`

Le pont tourne sur l'hôte du brain et joint l'instance par une façade TLS ; remplacez l'hôte par
le vôtre. `--check-config` n'ouvre rien, il contrôle donc sans hôte joignable.

<!-- combination 7 base=config.server.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: wss://instance-host.example/
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    instance_identity:
      twitch: ${TWITCH_BROADCASTER_ID}
    allowlist:
      - action: BRB
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combinaison 8 — le pont dans le processus agent, puits seul

Sur le PC de diffusion, l'agent sert l'action de run et `agent_link` la prête au brain. Sur l'hôte
du brain, ajoutez `automation.action.run` à `modules.proxy.actions` de
`config.server.yaml.example` et accordez-la là-bas avec la même règle `actions:` : le proxy trouve
la spécification de l'action de run dans le catalogue des manifests découverts, y compris le pont
désactivé sur cet hôte. Les familles et les cues n'atteignent pas le brain dans ce placement, donc
le fragment met les cues à `none`.

<!-- combination 8 base=agent.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    password: ${AUTOMATION_PASSWORD}
    companion_name: Companion
    allowlist:
      - action: BRB
  agent_link:
    actions:
      - automation.action.run
inputs:
  cues:
    '*': none
actions:
  - rule_id: brain-runs-instance-actions
    action_name: automation.action.run
    destination:
      platform: "*"
      channel_id: "*"
      scope: automation
    principals:
      - brain
    natures:
      - write
    granted_permissions:
      - automation.run
```

### Combinaison 9 — refusé : deux propriétaires pour une paire

<!-- combination 9 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    companion_name: Companion
    companion_identities:
      twitch: ${TWITCH_BOT_USER_ID}
inputs:
  chat:
    twitch: [twitch, streamerbot]
```

Diagnostic : `inputs: family 'chat': platform 'twitch': names more than one owner`

### Combinaison 10 — refusé : un propriétaire non activé

<!-- combination 10 base=config.yaml.example -->
```yaml
inputs:
  chat:
    twitch: streamerbot
```

Diagnostic : `inputs: family 'chat': platform 'twitch': owner 'streamerbot' is not enabled`

### Combinaison 11 — refusé : une URL `ws://` hors boucle locale

<!-- combination 11 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ws://192.0.2.10/
    companion_name: Companion
```

Diagnostic : `module 'streamerbot': field 'url': must use wss unless the host is a loopback address`

### Combinaison 12 — refusé : chat possédé sans identité du compagnon

<!-- combination 12 base=config.yaml.example -->
```yaml
enabled_modules:
  - streamerbot
modules:
  streamerbot:
    url: ${AUTOMATION_URL}
    companion_name: Companion
inputs:
  chat:
    twitch: streamerbot
```

Diagnostic : `module 'streamerbot': field 'companion_identities.twitch': is required: this module owns the chat input of platform twitch`
