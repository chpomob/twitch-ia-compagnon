# Documentation du développement

Le plan de développement historique (`plan.md` v1.0, `twitch-ia-companion-mvp`)
définit les étapes P1–P8 et leurs critères de validation ; la spec générale
historique (`spec.md` v1.0, `twitch-ia-companion-mvp`) décrit les exigences
v1 R1–R7 et v1 AC1–AC21. **Ces deux fichiers sont cités ici à leur dernier état
avant la phase 0, le commit `c5754e1`**, parce que la branche de phase 0 remplace
`plan.md` par le plan de la phase 0 : un lien relatif vers `../plan.md` ne
pointerait plus vers les sections P1–P8 référencées par cet index. Pour lire le
document cité : `git show c5754e1:plan.md` et `git show c5754e1:spec.md`.
Les specs séparées disponibles pour P6–P8 sont dans [steps/](steps/), déplacées
sans modification, y compris leur statut historique `draft`. Il n'existe pas de
spec séparée P1–P5 dans ce dépôt : se reporter aux sections correspondantes du
plan et à la spec générale. Le plan prévoit aussi P4A et une revue finale P9 ;
ce dernier jalon ne doit pas être considéré comme validé par cet index.

La phase 0 (spec `phase0-foundation` v1.0, exigences R1–R8 et AC1–AC33)
remplace une partie des exigences et critères de la spec v1. La section
[Versionnement de la spec historique](#versionnement-de-la-spec-historique-phase-0)
enregistre lesquels, par quoi ils sont remplacés et quels tests changent ; elle
ne réécrit ni les verdicts ni les livraisons ci-dessous. Pour distinguer les
deux vocabulaires, cet index préfixe toujours les identifiants : « v1 R4 »
désigne la spec historique, « phase 0 R6 » la spec de la phase 0.

La phase 1 (spec `phase1-agentic` v1.2, exigences R1–R8 et AC1–AC58) ne
remplace de la phase 0 que ce que son allowlist nomme. La section
[Versionnement de la spec phase 0 (phase 1)](#versionnement-de-la-spec-phase-0-phase-1)
l'enregistre de la même manière, et la section
[Essai de topologie de la phase 1](#essai-de-topologie-de-la-phase-1-phase-1-topology-trial)
consigne l'essai PC/serveur exigé avant de décrire cette topologie comme livrée.

La phase 2 (spec `phase2-audio-stream-interaction` v1.2, exigences R1–R10 et
AC1–AC43) ne remplace de la phase 1 que ce que son allowlist nomme — huit
tests — et une règle de l'exécuteur (R10). La section
[Versionnement de la spec phase 1 (phase 2)](#versionnement-de-la-spec-phase-1-phase-2)
l'enregistre, avec les échéances de la lecture et de la capture et les limites
déclarées ; la section
[Essais d'intégration par fournisseur (phase 2)](#essais-dintégration-par-fournisseur-phase-2)
consigne ce qui a réellement tourné contre chaque fournisseur réel.

La phase 3 (spec `phase3-presence-memory-platforms` v1.0, exigences R1–R8 et
AC1–AC42) ne remplace de la phase 2 que ce que son allowlist nomme — six
tests. La section
[Phase 3 — présence, mémoire des spectateurs, modération et plateformes](#phase-3--présence-mémoire-des-spectateurs-modération-et-plateformes)
l'enregistre, avec le pack de présence (configuration ou code), la matrice
des capacités par plateforme, le modèle de la mémoire, la modération, la
veille et les essais réels par plateforme.

## Étapes, livraisons et preuves

« Livraison » désigne ici le commit historique de l'étape, sans présumer sa
conformité. Les tests liés ci-dessous sont les fichiers de la branche courante ;
leur présence dans un ancien commit n'est pas une preuve d'exécution réussie.
La re-validation globale décrite plus bas porte sur l'état corrigé de `main`.

| Étape | Spec / périmètre dans le plan | Commit de livraison | Preuve disponible et limites |
| --- | --- | --- | --- |
| P1 — Bus asynchrone | Plan v1 à `c5754e1`, section P1 ; spec v1 à `c5754e1`, R1 | `66b0059` | Message de squash « adversarial approved » ; pas de manifest P1 conservé ici. [Tests bus](../tests/test_bus.py) inclus dans la re-validation globale. |
| P2 — Chargement des modules | Plan v1 à `c5754e1`, section P2 ; spec v1 à `c5754e1`, R2 | `63f5574` | Message de squash « adversarial approved » ; pas de manifest P2 conservé ici. [Tests loader](../tests/test_loader.py) inclus dans la re-validation globale. |
| P3 — Configuration et cycle de vie | Plan v1 à `c5754e1`, section P3 ; spec v1 à `c5754e1`, R3 | `7309e2e` | Message de squash « adversarial approved » ; pas de manifest P3 conservé ici. [Tests main](../tests/test_main.py) inclus dans la re-validation globale ; corrections ultérieures du shutdown et de l'annulation. |
| P4 — Réception Twitch EventSub | Plan v1 à `c5754e1`, section P4 ; spec v1 à `c5754e1`, R4 | `9996e0e` | Message de squash « adversarial approved » ; pas de manifest P4 conservé ici. [Tests Twitch](../tests/test_twitch.py) inclus dans la re-validation globale. Cette livraison ne contient pas P4A. |
| P4A — Envoi Twitch Helix | Plan v1 à `c5754e1`, section P4A ; spec v1 à `c5754e1`, R4 | `a0c3173` | Complément livré après P8 ; [tests Twitch](../tests/test_twitch.py), [brain](../tests/test_brain.py) et [intégration](../tests/test_integration.py) inclus dans la re-validation globale. |
| P5 — Brain / pipeline LLM | Plan v1 à `c5754e1`, section P5 ; spec v1 à `c5754e1`, R5 | `965b250` | Aucun manifest P5 conservé ici, ni rapport d'exécution historique séparé. [Tests brain](../tests/test_brain.py) inclus dans la re-validation globale. |
| P6 — Middleware audit | [Spec P6](steps/step-P6-spec.md) ; plan v1 à `c5754e1`, section P6 | `bd6e7a3` | Trois runs conservés : REJECT, interrompu, INFRA_FAILURE ; aucun ne prouve l'approbation de cette livraison. [Tests audit](../tests/test_audit.py) inclus dans la re-validation globale. |
| P7 — Exemple de configuration et cohérence des manifests de modules | [Spec P7](steps/step-P7-spec.md) ; plan v1 à `c5754e1`, section P7 | `f3d07fc` | Run conservé : INFRA_FAILURE, sans approbation démontrée. [Tests examples](../tests/test_examples.py) inclus dans la re-validation globale. |
| P8 — Intégration de bout en bout | [Spec P8](steps/step-P8-spec.md) ; plan v1 à `c5754e1`, section P8 | `fcc63c9` | [Manifest APPROVED](runs/8fd87afb-b05f-4ea6-b303-e3e57edfa624/manifest.md) : verdict historique du pipeline uniquement. L'arbre livré était cassé ; voir ci-dessous. [Tests integration](../tests/test_integration.py) inclus dans la re-validation globale après corrections. |

## P8 : verdict historique et arbre livré

Le manifest P8 enregistre un verdict `APPROVED` le 3 septembre 2026. Il est
conservé comme preuve du verdict émis par le pipeline à cette époque, **pas
comme preuve de conformité de l'arbre final `fcc63c9`**. Ce commit livrait un
arbre cassé : la collecte pytest échouait, car les tests référençaient
`HELIX_CHAT_URL`, absent du module Twitch. P4A (envoi chat Helix), pourtant prévu
par le plan et requis par P7/P8, n'avait jamais été commité.

Le libellé « adversarial approved » du squash ne corrige pas cette divergence.
Ni le manifest ni les specs historiques ne sont réécrits pour masquer ce défaut.

## Corrections et re-validation sur main

Les corrections suivantes ont été livrées après P8, dans cet ordre :

| Commit | Correction |
| --- | --- |
| `a0c3173` | Complète P4A : envoi chat Helix avec résultats de livraison explicites. |
| `261f985` | Ignore les notifications de chat émises par le bot lui-même. |
| `a4871ce` | Termine les événements en cours avant un arrêt à durée bornée. |
| `c0632b7` | Nettoie le démarrage partiel lors d'une annulation. |

La suite complète a été signalée verte à chaque étape de cette série de
corrections. Le 8 septembre 2026, lors de ce rangement documentaire, la commande
`python3 -m pytest tests/ -q` a été réexécutée sur `main`, à partir de
`c0632b7c6ff3d85cab4bae8b00bd80edd866a317` : **189 passed**.
Cela valide l'exécution de la suite sur l'état corrigé, sans valider
rétroactivement `fcc63c9`, ni constituer à lui seul une preuve de revue P9 ou un
essai avec les services réels. Le commit de rangement ne change que la documentation.

## Versionnement de la spec historique (phase 0)

La phase 0 (branche `feat(phase0)`, commits `623d552` à `b9e08da` pour P1–P22,
spec `phase0-foundation` v1.0 issue de [design-v2.md](design-v2.md) section 6)
change une partie du comportement que la spec v1 (`spec.md` v1.0 à `c5754e1`)
exigeait et que les tests P1–P8 vérifiaient. Cette section enregistre **ce qui
change et par quoi**, pas une nouvelle conformité : les verdicts, livraisons et
limites des sections précédentes restent valables pour l'état `c5754e1` et ne
sont pas réécrits. Les exigences v1 non citées ici (v1 R2, et les critères v1
AC1, AC2, AC4–AC6, AC9, AC11–AC13, AC18) ne sont pas versionnées par la phase 0 ;
la spec de phase 0 ne les remplace pas, et les tests qui les portent ont été
adaptés au nouveau contexte d'activation (fixture de contexte d'exécution,
payload v2) sans figurer dans l'allowlist. La revue de branche P24 n'est pas
consignée par cet index.

### Exigences v1 remplacées

| Spec v1 | Ce que la v1 exigeait | Remplacée par (phase 0) | Ce qui change | Tests de l'allowlist concernés |
| --- | --- | --- | --- | --- |
| v1 R1 (inspection de l'historique du bus) | `list_events()` expose toute publication finalisée, sans borne. | Phase 0 R6, AC20 (`b7b863a`, P3 ; `09c6916`, P20) | L'historique est borné en nombre d'événements, en octets et en âge, éviction du plus ancien ; abonnement, ordre, remplacement et erreurs de publication inchangés. | Aucun. Les assertions v1 de [test_bus.py](../tests/test_bus.py) sont conservées, complétées par les tests d'historique borné (`test_byte_limit_evicts_before_count_limit_without_failing_publications`, `test_age_limit_evicts_on_append_and_on_read_with_injected_clock`, `test_full_history_never_fails_a_publication`) et par `test_ac20_*` dans [test_retention.py](../tests/test_retention.py). |
| v1 R3 (réglages requis détenus par le cœur) | `core/main.py` exige `brain.endpoint/model/api_key` et `twitch.client_id/client_secret/access_token/broadcaster_id/bot_user_id` depuis sa propre table. | Phase 0 R7, AC24–AC25 (`0493771`, P12 ; `635c3a0`, P15 ; `70ce389`, P14 ; `898a428`, P17) | `_MODULE_REQUIRED_FIELDS` est supprimé ; chaque module valide ses réglages par un hook déclaré dans son manifest, tous les modules activés avant tout effet réseau ; le cœur ne valide que structure, références d'environnement, chemins et versions. | `tests/test_main.py::test_each_type_invalid_required_module_setting_is_rejected`, `tests/test_main.py::test_missing_required_setting_fails_before_readiness_without_values`. |
| v1 R4 (dédoublonnage sur toute l'activation) | Un identifiant de message déjà traité pendant l'activation courante, y compris après reconnexion, ne produit plus jamais d'événement. | Phase 0 R6, AC22 (`70ce389`, P14 ; `09c6916`, P20) ; forme du payload : phase 0 R3, AC12 | La garantie ne couvre plus que la fenêtre retenue (bornée en entrées et en TTL) ; une répétition arrivant après éviction est retraitée et l'éviction est comptée. Le payload est normalisé en `schema_version: 2` (`platform`, `channel_id`, `author.id`, `message_id`, `text`) au lieu de `broadcaster_id`/`chatter_id`/`chatter_name`. | Aucun sur le dédoublonnage. `test_notification_mapping_dedup_and_retry_reconnect` est conservé (la répétition reste dans la fenêtre) avec le payload v2 ; la borne est vérifiée par `tests/test_twitch.py::test_dedup_window_is_bounded_by_entries_and_ttl` et `test_ac22_*` dans [test_retention.py](../tests/test_retention.py). Les tests brain allowlistés qui alimentaient un payload v1 `chatter_id` sont listés sous v1 AC10. |
| v1 R5 (livraison par balises) | Le brain découpe la sortie du modèle en sections `[send:<event.type>]` et republie chaque section comme événement ; la livraison se lit sur le bus. | Phase 0 R5, AC16–AC19 (`d3ff3f3`, P7 ; `00dc9e0`, P16) ; admission : phase 0 R2 ; clé de session : phase 0 R3 | Plus d'encodage par balises : une réponse texte, livrée par exactement un appel de l'exécuteur d'actions vers un fournisseur lié, autorisée par défaut-refus, avec un statut terminal explicite ; la route `channel.chat.send` reste une compatibilité vers le même service d'envoi. | Les douze tests `tests/test_brain.py::*` de l'allowlist (voir la table ci-dessous). |
| v1 R6 (audit complet sous surcharge) | Un enregistrement pour **chaque** événement qui atteint l'audit ; la file est illimitée et rien n'est perdu. | Phase 0 R6, AC21 (`898a428`, P17 ; `d75b5f2`, P19 ; `09c6916`, P20) | La file est bornée en enregistrements et en octets ; à saturation l'enregistrement nouvellement offert est **abandonné**, compté par un compteur de pertes rapporté hors de la file ; la publication n'est jamais bloquée ni perdue. La redaction v1 est conservée. | Aucun directement (l'allowlist audit ne porte que sur le manifest, sous v1 R7). Vérifié par `tests/test_audit.py::test_a_blocked_writer_saturating_the_queue_drops_new_records_and_counts_the_loss` et `test_ac21_*` dans [test_retention.py](../tests/test_retention.py). |
| v1 R7 (forme des manifests) | Trois manifests à quatre ou cinq clés (`name`, `produces`, `consumes`, `middleware`, `order`) comparés par égalité ; l'exemple de configuration porte les seuls réglages v1. | Phase 0 R7, AC26 (`5a26827`, P11 ; `f1a7c49`, P13 ; `635c3a0`, P15 ; `898a428`, P17 ; `bb36fae`, P18) ; rôle de flush : phase 0 R4 | Les manifests déclarent `manifest_version`, `runtime_api`, `settings_schema`, `settings_validator`, `lifecycle.roles`, et pour l'entrée chat `triggers` et `actions` ; un manifest sans `manifest_version` reste v1. L'exemple de configuration ajoute nom du compagnon, politiques de trigger, limites d'admission et de rétention, règles d'autorisation. | `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink`, `tests/test_brain.py::test_manifest_declares_brain_source_and_route`, `tests/test_audit.py::test_manifest_declares_catch_all_middleware_at_order_90`, `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities`. |

### Critères d'acceptation v1 remplacés

| Critère v1 | Remplacé par (phase 0) | Ce qui change | Tests de l'allowlist concernés |
| --- | --- | --- | --- |
| v1 AC3 (R1) : l'entrée `list_events()` observe le remplacement complet | Phase 0 R6, AC20 | Le remplacement est toujours observé dans l'historique, mais une entrée peut ensuite être évincée par la borne ; les erreurs de publication gardent leurs sémantiques sous saturation. | Aucun (voir v1 R1). |
| v1 AC7 (R3) : 3 modules MVP activés, une opportunité d'arrêt chacun, sortie `0` | Phase 0 R4, AC13 | L'arrêt suit les phases déclarées par chaque manifest (stop des producteurs, drain, close, flush de l'observation) au lieu d'un ordre `twitch, brain, audit` codé par nom dans le cœur. | `tests/test_main.py::test_valid_config_waits_for_stop_and_closes_producers_before_audit`. |
| v1 AC8 (R3) : un jeton Twitch manquant est rejeté par le cœur avant la disponibilité | Phase 0 R7, AC24 | Le rejet vient du hook de validation du module, nomme module et champ, sans valeur secrète ; le YAML illisible, la référence non résolue et l'exception d'activation restent rejetés par le cœur. | `tests/test_main.py::test_each_type_invalid_required_module_setting_is_rejected`, `tests/test_main.py::test_missing_required_setting_fails_before_readiness_without_values`. |
| v1 AC10 (R4) : payload `broadcaster_id`/`chatter_id`/`chatter_name`, rejeu d'un même ID sans nouvel événement pendant toute l'activation | Phase 0 R3, AC12 (normalisation) ; phase 0 R6, AC22 (fenêtre bornée) | Payload `schema_version: 2` ; le rejeu est absorbé seulement à l'intérieur de la fenêtre retenue. | `tests/test_brain.py::test_configured_request_contains_capabilities_and_viewer_context`, `tests/test_brain.py::test_configured_request_strips_string_settings` (alimentaient un payload v1 `chatter_id`). |
| v1 AC14 (R5) : 1 requête modèle sur l'endpoint et le modèle configurés ; le contexte système nomme les capacités d'événements d'entrée et de sortie du catalogue (les 3 manifests MVP) ; le contexte utilisateur garde le texte et l'identifiant du spectateur | Phase 0 R5 (vue autorisée des actions), AC17, AC19 ; identité du spectateur : phase 0 R3, AC12 | L'unique requête, l'endpoint et le modèle configurés, et la conservation du texte et de l'identifiant du spectateur sont inchangés. Le contexte système ne décrit plus le catalogue d'événements : il n'offre que la vue **autorisée** du registre d'actions, relue à chaque run pour la destination et le principal du brain (sans règle applicable, il déclare qu'aucune action n'est autorisée, et une action seulement déclarée ou liée n'y figure pas) ; l'identifiant du spectateur est l'`author.id` attesté du payload v2, non plus `chatter_id`. | `tests/test_brain.py::test_configured_request_contains_capabilities_and_viewer_context` (remplacé par `test_admitted_message_drives_one_configured_request_with_viewer_context`, qui affirme la vue autorisée et l'absence de balise `[send:`), `tests/test_brain.py::test_configured_request_strips_string_settings` ; hors allowlist, la vue par défaut-refus et sa relecture sont vérifiées par `tests/test_brain.py::test_request_offers_no_action_without_a_grant_and_reads_the_view_afresh`. |
| v1 AC15 (R5) : `[send:channel.chat.send]Hello there` publie 1 `channel.chat.send` | Phase 0 R5, AC19 | Un message admis donne exactement 1 appel modèle, 1 appel exécuteur, 1 requête d'envoi ; le texte est livré tel quel, sans analyse de balise. | `tests/test_brain.py::test_tagged_output_is_published_with_source_context`, `tests/test_brain.py::test_multiple_sections_preserve_source_order`, `tests/test_brain.py::test_malformed_send_prefix_in_section_body_is_plain_text`, `tests/test_brain.py::test_invalid_tagged_output_is_rejected_atomically`. |
| v1 AC16 (R5) : 2 sections → 2 événements ; échecs → 0 événement et un diagnostic assaini | Phase 0 R5, AC18 ; phase 0 R2 | Une réponse = une livraison ; les échecs sont l'état terminal du run (`error`, `timeout`, `refused`, …), lus sur son enregistrement et non sur le retour synchrone de la publication ; le diagnostic reste assaini. | Les quatre tests ci-dessus, plus `tests/test_brain.py::test_model_failures_publish_nothing_and_are_sanitized`, `tests/test_brain.py::test_partial_publish_failure_remembers_only_delivered_sections`, `tests/test_brain.py::test_delivery_outcome_excludes_refused_sections_from_history`. |
| v1 AC17 (R6) : chaque événement atteignant l'audit émet exactement 1 enregistrement | Phase 0 R6, AC21 | Sous saturation de la file bornée, 0 enregistrement pour l'événement offert et +1 au compteur de pertes ; hors saturation, toujours 1 enregistrement identique à l'événement reçu. | Aucun (voir v1 R6). |
| v1 AC19 (R7) : 3 manifests déclarant exactement les directions de capacité v1 | Phase 0 R7, AC26 | Les directions `produces`/`consumes`, `middleware` et `order: 90` sont inchangées mais ne sont plus l'intégralité du manifest ; l'égalité stricte est remplacée par une égalité sur les clés v1 plus la vérification des déclarations v2. | Les quatre tests de manifest listés sous v1 R7. |
| v1 AC20 (R7) : `config.yaml.example` porte exactement les réglages v1 | Phase 0 R1, R2, R5, R6 (sections `triggers`, `admission`, `retention`, `authorization`, `companion`) ; phase 0 AC32 | L'exemple reste analysable, active les mêmes trois modules, garde les références `${NAME}` v1 et 0 secret ; il porte en plus les réglages que le runtime versionné exige, tous finis. | `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` (la vérification de l'exemple lui-même, `test_example_config_is_complete_and_contains_no_literal_credentials`, n'est pas allowlistée et reste en place, complétée par `test_example_config_carries_what_the_versioned_runtime_requires` et `test_example_config_satisfies_every_module_owned_settings_validator`). |
| v1 AC21 (R7) : 1 notification traverse source, brain, route `channel.chat.send` par balise, puits ; l'audit enregistre l'entrée et la sortie | Phase 0 R8, AC27 ; phase 0 R1, R2 | Le message doit d'abord être accepté par une politique de trigger ; le run est détaché de la chaîne de publication ; l'audit voit, sous un seul `run_id`, les traces `input.trigger.accepted`, `brain.admission.accepted`, `brain.run.started`, `action.started`/`action.completed`, `channel.chat.sent`, `brain.run.completed` et non plus exactement 2 enregistrements. | `tests/test_integration.py::test_example_config_drives_full_chat_pipeline_and_clean_shutdown`, `tests/test_integration.py::test_failed_helix_publication_is_sanitized_and_next_one_succeeds`. |

### Allowlist de la spec phase 0 : sort de chaque test

Les 23 entrées de l'allowlist de la spec `phase0-foundation` (section « Test
failure allowlist ») sont reprises ici avec ce qui leur est arrivé sur la
branche. « Réécrit en place » signifie que le nom est conservé et que le corps
affirme la garantie de la phase 0 ; « remplacé par » signifie que le test v1
a été supprimé et qu'un test nommé différemment porte la garantie qui le
remplace. Dans les deux cas la docstring du test cite l'exigence qui le
remplace. Aucune entrée n'a été neutralisée par `skip`, `xfail` ou
affaiblissement d'assertion.

| # | Test v1 allowlisté | Sort | Test porteur sur la branche | Exigence(s) phase 0 | Commit |
| --- | --- | --- | --- | --- | --- |
| 1 | `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink` | Réécrit en place | même nom | R7, R1, R5 | `f1a7c49` (P13) |
| 2 | `tests/test_brain.py::test_manifest_declares_brain_source_and_route` | Remplacé par | `tests/test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant` | R7 | `635c3a0` (P15) |
| 3 | `tests/test_audit.py::test_manifest_declares_catch_all_middleware_at_order_90` | Réécrit en place | même nom | R7, R4 | `d75b5f2` (P19) |
| 4 | `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Réécrit en place | même nom | R7 | `09c6916` (P20) |
| 5 | `tests/test_main.py::test_valid_config_waits_for_stop_and_closes_producers_before_audit` | Remplacé par | `tests/test_main.py::test_valid_config_waits_for_stop_and_unwinds_declared_phases_in_order` | R4, AC13 | `0493771` (P12) |
| 6 | `tests/test_main.py::test_each_type_invalid_required_module_setting_is_rejected` | Remplacé par | `tests/test_main.py::test_each_type_invalid_module_setting_is_rejected_by_the_module_hook` | R7, AC24 | `0493771` (P12) |
| 7 | `tests/test_main.py::test_missing_required_setting_fails_before_readiness_without_values` | Remplacé par | `tests/test_main.py::test_missing_setting_fails_before_readiness_naming_module_and_field` | R7, AC24 | `0493771` (P12) |
| 8 | `tests/test_shutdown.py::test_shutdown_drains_real_pipeline` | Réécrit en place (paramétré) | même nom | R2, R4, R8 | `00dc9e0` (P16) |
| 9 | `tests/test_shutdown.py::test_brain_close_waits_for_handler_not_long_lived_caller` | Remplacé par | `tests/test_shutdown.py::test_brain_close_ends_the_held_run_without_blocking_the_publisher` | R2, R4 | `00dc9e0` (P16) |
| 10 | `tests/test_brain.py::test_configured_request_contains_capabilities_and_viewer_context` | Remplacé par | `tests/test_brain.py::test_admitted_message_drives_one_configured_request_with_viewer_context` | R1, R2, R3, R5 | `00dc9e0` (P16) |
| 11 | `tests/test_brain.py::test_configured_request_strips_string_settings` | Réécrit en place | même nom | R1, R2, R3, R7 | `00dc9e0` (P16) |
| 12 | `tests/test_brain.py::test_tagged_output_is_published_with_source_context` | Remplacé par | `tests/test_brain.py::test_reply_is_delivered_through_exactly_one_executor_call_and_one_send` | R2, R5, AC19 | `00dc9e0` (P16) |
| 13 | `tests/test_brain.py::test_multiple_sections_preserve_source_order` | Remplacé par | `tests/test_brain.py::test_reply_text_is_delivered_verbatim_with_no_tag_parsing` | R5 | `00dc9e0` (P16) |
| 14 | `tests/test_brain.py::test_malformed_send_prefix_in_section_body_is_plain_text` | Remplacé par | `tests/test_brain.py::test_reply_text_is_delivered_verbatim_with_no_tag_parsing` | R5 | `00dc9e0` (P16) |
| 15 | `tests/test_brain.py::test_invalid_tagged_output_is_rejected_atomically` | Remplacé par | `tests/test_brain.py::test_reply_text_is_delivered_verbatim_with_no_tag_parsing` | R5 | `00dc9e0` (P16) |
| 16 | `tests/test_brain.py::test_model_failures_publish_nothing_and_are_sanitized` | Remplacé par | `tests/test_brain.py::test_model_failures_deliver_nothing_and_are_sanitized` | R2 | `00dc9e0` (P16) |
| 17 | `tests/test_brain.py::test_history_is_retained_per_viewer_and_session_close_is_idempotent` | Remplacé par | `tests/test_brain.py::test_memory_is_keyed_by_session_key_not_by_viewer` | R2, R3, AC10 | `00dc9e0` (P16) |
| 18 | `tests/test_brain.py::test_model_requests_for_different_viewers_can_overlap` | Remplacé par | `tests/test_brain.py::test_model_requests_for_different_sessions_overlap_within_the_worker_cap` | R2 | `00dc9e0` (P16) |
| 19 | `tests/test_brain.py::test_viewer_histories_are_lru_bounded` | Remplacé par | `tests/test_brain.py::test_memory_is_keyed_by_session_key_not_by_viewer` | R3, AC10 | `00dc9e0` (P16) |
| 20 | `tests/test_brain.py::test_partial_publish_failure_remembers_only_delivered_sections` | Remplacé par | `tests/test_brain.py::test_only_a_success_observation_writes_back_to_memory` | R5 | `00dc9e0` (P16) |
| 21 | `tests/test_brain.py::test_delivery_outcome_excludes_refused_sections_from_history` | Remplacé par | `tests/test_brain.py::test_only_a_success_observation_writes_back_to_memory` | R5 | `00dc9e0` (P16) |
| 22 | `tests/test_integration.py::test_example_config_drives_full_chat_pipeline_and_clean_shutdown` | Remplacé par | `tests/test_integration.py::test_ac27_example_config_drives_one_correlated_run_and_clean_shutdown` | R1, R2, R8, AC27 | `b9e08da` (P22) |
| 23 | `tests/test_integration.py::test_failed_helix_publication_is_sanitized_and_next_one_succeeds` | Remplacé par | `tests/test_integration.py::test_r8_refused_send_is_sanitized_leaves_no_send_fact_and_next_send_succeeds` | R1, R2, R8 | `b9e08da` (P22) |

Le 12 septembre 2026, lors de cet enregistrement documentaire, la commande
`python3 -m pytest tests/ -q -p no:cacheprovider` a été réexécutée sur la
branche à partir de `b9e08da` (P22) : **636 passed**. Comme pour la re-validation
de `c0632b7` plus haut, cela valide l'exécution de la suite telle qu'elle existe
sur la branche ; cela ne constitue pas la revue de branche P24, ni un essai avec
les services réels, et ne modifie pas les verdicts historiques de P1–P8.

## Versionnement de la spec phase 0 (phase 1)

La phase 1 (branche `feat(phase1)`, commits `7a08d6c` à `be9e539` pour P1–P22,
spec `phase1-agentic` v1.2 archivée dans
[campaigns/phase1/spec.md](campaigns/phase1/spec.md), plan dans
[campaigns/phase1/plan.md](campaigns/phase1/plan.md), issue de
[design-v2.md](design-v2.md) §3.1–3.4, §4.1–4.4 et §6) ne remplace rien de la
phase 0 **sauf ce que son allowlist nomme** : quatre tests. Cette section
enregistre, comme la précédente, **ce qui change et par quoi**, pas une
nouvelle conformité : les verdicts, livraisons et limites des sections
précédentes restent valables pour les états qu'elles citent. Les exigences de
la phase 0 non citées ici restent en vigueur telles quelles (admission bornée,
cycle de vie par phases, statut terminal explicite des actions, autorisation
par défaut-refus lectures comprises, rétention bornée, délai global unique de
démarrage/arrêt, redaction des secrets configurés) ; les adaptations du
harnais que la spec de phase 1 liste comme « non allowlistées » (réponse à la
sonde de capacités, forme des requêtes par outils) conservent leur garantie et
ne sont pas versionnées. La revue de branche P24 n'est pas consignée par cet
index. Vocabulaire : « phase 0 R5 » désigne la spec `phase0-foundation`,
« phase 1 R1 » la spec `phase1-agentic`.

### Exigences phase 0 remplacées

| Spec phase 0 | Ce que la phase 0 exigeait | Remplacée par (phase 1) | Ce qui change | Tests de l'allowlist concernés |
| --- | --- | --- | --- | --- |
| Phase 0 R5 (livraison par le brain) | Au plus un appel modèle par travail admis ; le contexte système offre au modèle la vue **autorisée** du registre d'actions (l'action de livraison `chat.write` comprise, dès qu'elle est accordée) ; la livraison est un appel de l'exécuteur vers l'action codée en dur `DELIVERY_ACTION = "chat.write"` dans `modules/brain/__init__.py`, son statut lu sur le résultat explicite de l'exécuteur. | Phase 1 R1, décision 1, AC1, AC6, AC46 (`7471842`, P10 ; `6054fce`, P11 ; `033303e`, P12 ; `a1f119c`, P13) | Boucle agentique multi-tours : le modèle ne reçoit, **comme outils**, que les actions autorisées de nature `read`, relues à chaque tour ; `chat.write` n'est jamais offert, même accordé (une proposition qui le nomme donne une observation `refused` synthétique, 0 appel exécuteur). La livraison devient une **étape terminale configurée et enfichable** : une liste ordonnée d'actions de livraison (mode `fixed`, ou mode `modules` dérivé des modules activés déclarant une capacité de livraison, avec ordre de préférence), chaque entrée déclarant comment le texte final entre dans ses arguments (argument nommé, ou aucun pour un effet pur), résolue à `prepare`, exécutée une fois et dans l'ordre à la fin du run seulement, un appel exécuteur par entrée ; le modèle ne choisit jamais la livraison et n'exécute aucun effet intermédiaire ; ajouter un module de livraison ne touche pas la boucle. La constante `DELIVERY_ACTION` est supprimée (`6054fce`). Inchangés : défaut-refus de l'exécuteur à chaque appel, statut terminal explicite, route de compatibilité `channel.chat.send`. | `tests/test_brain.py::test_admitted_message_drives_one_configured_request_with_viewer_context`, `tests/test_brain.py::test_request_offers_no_action_without_a_grant_and_reads_the_view_afresh`. |
| Phase 0 R7 (manifests v2) et v1 R7 reconduit par la phase 0 (exemple de configuration) | `config.yaml.example` active les trois modules MVP `twitch`, `brain`, `audit` et ses règles d'autorisation accordent exactement les actions que leurs manifests déclarent, soit {`chat.write`}. | Phase 1 R7, AC40–AC42 (`71597c4`, P21) | `config.yaml.example` devient le **profil PC** à six modules (`twitch`, `chat_context`, `users`, `capture`, `brain`, `audit`) ; ses règles accordent exactement son ensemble d'actions fournies {`chat.read`, `users.read`, `screen.capture`, `chat.write`}. Deux profils s'ajoutent sur le même `run()` : `config.server.yaml.example` (serveur : `proxy` à la place de `capture`, `screen.capture` fourni par l'allowlist `actions` du proxy et par aucun manifest activé) et `agent.yaml.example` (agent PC : `capture`, `agent_link`, {`screen.capture`}). Les deux profils brain portent le groupe `delivery` en `mode: fixed` avec l'unique entrée `{action: chat.write, text_argument: text}` : leur livraison observable reste un `chat.write` par run. Forme des manifests v2, validation par hook de module, 0 secret littéral et références `${NAME}` : inchangés. | `tests/test_examples.py::test_example_config_is_complete_and_contains_no_literal_credentials`, `tests/test_examples.py::test_example_authorization_grants_exactly_the_actions_the_modules_declare`. |

### Critères d'acceptation phase 0 remplacés

| Critère phase 0 | Remplacé par (phase 1) | Ce qui change | Tests de l'allowlist concernés |
| --- | --- | --- | --- |
| Phase 0 AC19 (R5) : 1 message admis → exactement 1 appel modèle, 1 appel exécuteur, 1 requête d'envoi au transport fictif | Phase 1 AC1, AC6, AC46 | Un message admis donne autant de tours modèle que de propositions plus la réponse finale (bornés par les budgets de R3), un appel exécuteur par proposition `read` exécutée **plus** un par entrée de la liste de livraison, et toujours exactement 1 envoi au transport fictif — après la réponse finale, jamais avant. L'unicité de l'envoi externe et l'exclusivité route de compatibilité / exécuteur sont conservées. | Les deux tests `tests/test_brain.py::*` listés sous phase 0 R5. |
| Phase 0 R5, vue autorisée offerte au modèle (assertion de `tests/test_brain.py` : l'action de livraison accordée figure dans le contexte système) | Phase 1 AC6 | Le contexte système ne contient ni balise `[send:` ni outil `chat.write` ; la vue autorisée offerte est celle des actions `read` seulement, avec leur nom, description et schéma d'arguments comme définitions d'outils. Un grant `chat.write` change la livrabilité, jamais les outils offerts. | Les deux mêmes tests. |
| v1 AC20 reconduit par la phase 0 : l'exemple active les mêmes trois modules et accorde exactement ce que leurs manifests déclarent | Phase 1 AC42 (et AC40 pour `--check-config`) | Trois fichiers d'exemple, chacun à 0 identifiant littéral, chacun accordant exactement son ensemble d'actions fournies (manifests activés ∪ allowlist `actions` d'un `proxy` activé) ; chacun validé par `--check-config` avec sortie 0 et 0 socket ouvert. | Les deux tests `tests/test_examples.py::*` listés sous phase 0 R7. |

### Allowlist de la spec phase 1 : sort de chaque test

Les 4 entrées de l'allowlist de la spec `phase1-agentic` (section « Test
failure allowlist ») sont reprises ici avec ce qui leur est arrivé sur la
branche, avec le même vocabulaire que pour la phase 0 : « réécrit en place »
conserve le nom, « remplacé par » supprime le test et nomme celui qui porte
la garantie qui le remplace. Dans les deux cas la docstring cite l'exigence
de remplacement. Aucune entrée n'a été neutralisée par `skip`, `xfail` ou
affaiblissement d'assertion.

| # | Test allowlisté (phase 0) | Sort | Test porteur sur la branche | Exigence(s) phase 1 | Commit |
| --- | --- | --- | --- | --- | --- |
| 1 | `tests/test_brain.py::test_admitted_message_drives_one_configured_request_with_viewer_context` | Réécrit en place (l'assertion `DELIVERY_ACTION in system` devient : les outils offerts sont la vue `read` autorisée, la livraison jamais offerte) | même nom | R1, AC6, décision 1 | `7471842` (P10), complété par `6054fce` (P11) et `033303e` (P12) |
| 2 | `tests/test_brain.py::test_request_offers_no_action_without_a_grant_and_reads_the_view_afresh` | Réécrit en place (l'assertion que le grant `chat.write` atteint le prompt devient : un grant `read` atteint les outils du tour suivant, le grant de livraison n'atteint aucune requête) | même nom | R1, AC6, décision 1 | `7471842` (P10), complété par `6054fce` (P11) et `033303e` (P12) |
| 3 | `tests/test_examples.py::test_example_config_is_complete_and_contains_no_literal_credentials` | Remplacé par | `tests/test_examples.py::test_profile_enables_its_modules_and_contains_no_literal_credentials` (paramétré sur les trois profils) | R7, AC42 | `71597c4` (P21) |
| 4 | `tests/test_examples.py::test_example_authorization_grants_exactly_the_actions_the_modules_declare` | Remplacé par | `tests/test_examples.py::test_profile_grants_exactly_its_provided_action_set` (paramétré sur les trois profils) | R5, R7, AC42 | `71597c4` (P21) |

## Essai de topologie de la phase 1 (Phase 1 topology trial)

La topologie PC/serveur de la phase 1 (brain sur une machine, agent de
capture sur le PC du streamer, proxy WebSocket JSON entre les deux, spec
phase 1 R6–R7) n'est décrite comme livrée qu'accompagnée de cet
enregistrement (phase 1 R8, AC44). L'essai est le test à deux processus réels
`tests/test_proxy_process.py::test_ac39_two_process_topology_over_loopback`
(P22, AC39), exécuté le 20 septembre 2026 avec
`.venv/bin/python -m pytest tests/test_proxy_process.py -q -p no:cacheprovider`.
Les six champs ci-dessous sont ceux que R8 exige ; l'AC44 est vérifié par
`tests/test_hygiene.py`.

| Champ | Valeur enregistrée |
| --- | --- |
| Commit | `be9e539` (P22, arbre sur lequel l'essai a été exécuté et cet enregistrement écrit). La revue de branche P24 réexécute l'essai sur son commit de porte et met ce champ à jour. |
| Profils utilisés | Les deux profils livrés, dérivés sans changer leurs budgets, limites ni liste de livraison : **brain** = `config.server.yaml.example` (module `twitch` remplacé par le module fixture `fakeplatform`, dont le flux scripté émet deux messages à chaque appairage ; endpoint modèle pointé sur un faux serveur Chat Completions scripté tenu par le test ; audit vers un fichier temporaire ; section `tls` du proxy retirée, écoute `127.0.0.1:<port libre>` ; `modules_directory` = copie temporaire des modules livrés plus la fixture) ; **agent** = `agent.yaml.example` (`brain_url: ws://127.0.0.1:<port>`, deux sources de capture : `file` sur un PNG 16×9 et `command` sur un script « à verrou » que le test libère ou laisse orphelin ; jeton d'appairage par référence `${PROXY_PAIRING_TOKEN}`). |
| Hôtes | **Loopback**, pas d'hôtes distincts : les processus brain, agent, agent intrus et agent redémarré sont quatre enfants `python -m core.main --config <fichier>` du processus de test, sur la même machine, reliés par `127.0.0.1`. |
| TLS | **Non utilisé** : `ws://` sur loopback, ce que le validateur du proxy n'accepte que pour une adresse de loopback (une écoute non loopback sans `tls` est refusée à la validation, `tests/test_proxy.py`). Aucun certificat n'a été chargé ; le chemin `wss://` n'est couvert que par la validation des réglages et par le document de protocole. |
| Résultat observé | **1 passed** (0,73 s d'appel). Dans l'ordre : le brain se déclare prêt après les 2 sondes de capacités ; un agent présentant un mauvais jeton reçoit `auth_failed` (sa sortie d'erreur ne contient aucun jeton) et un appel direct avec ce jeton est fermé avec le code 4401, sans qu'aucun événement le nomme sur le bus ; l'agent valide s'appaire, ce qui déclenche le flux : le run « fichier » se termine avec l'image transférée **par référence** (identifiant d'attachement, jamais un chemin) et reçue par le modèle par valeur ; le run « verrou » démarre sa capture, l'agent est tué (SIGKILL) capture en vol, le run observe `error proxy_disconnected` sans partie image et répond quand même ; l'agent redémarré s'appaire de nouveau, les deux runs réussissent une seconde fois (capture libérée par le verrou) ; les deux processus sortent en `0` sur SIGTERM. Le fichier d'audit, lu après leur sortie, montre 2 appairages du seul agent valide, 4 runs `success`, les transitions `module.degraded`/`module.ready` du proxy autour de la coupure, et ne contient ni jeton ni chemin de fichier. |
| Limites restantes | Aucun hôte distinct ni réseau réel (loopback seulement) ; pas de TLS de bout en bout ; entrée `fakeplatform` scriptée, pas de plateforme réelle ni de Twitch ; modèle factice scripté (pas d'endpoint réel, pas de vision réelle) ; capture = un PNG fixe et un script Python, pas d'écran ; un seul agent, un seul canal, un seul spectateur, deux messages par appairage ; livraison observée sur le transport fictif de la plateforme fixture ; aucune mesure de latence, de débit ni de tenue dans le temps ; dépend d'un port loopback libre (le module se saute sinon, en nommant la raison) et de signaux POSIX. Un essai sur deux hôtes distincts avec `wss://` reste à faire avant de décrire la topologie comme éprouvée hors laboratoire. |

## Versionnement de la spec phase 1 (phase 2)

La phase 2 (branche `feat(phase2)`, commits `25c152e` (P1) à `23e6e91` (P19),
spec `phase2-audio-stream-interaction` v1.3 archivée dans
[campaigns/phase2/spec.md](campaigns/phase2/spec.md), plan dans
[campaigns/phase2/plan.md](campaigns/phase2/plan.md), issue de
[design-v2.md](design-v2.md)) ajoute la voix (synthèse et lecture,
`audio.speak`/`audio.play`), l'écoute (capture et transcription optionnelle,
`audio.capture`), les scènes et les sondages de diffusion
(`stream.scene.set`/`stream.poll.create`). Elle ne remplace de la phase 1
**que ce que son allowlist nomme** — douze tests : les huit de la v1.2,
réécrits en place, et quatre ajoutés par la réconciliation v1.3 (P22F2) — et
une règle de l'exécuteur, la précédence de R10 ci-dessous. Comme les sections
précédentes, celle-ci enregistre **ce qui change et par quoi**, pas une
nouvelle conformité : les garanties de la phase 0 et de la phase 1 restent en
vigueur (admission bornée, cycle de vie par phases, statut terminal explicite,
défaut-refus lectures comprises, rétention bornée, délai global unique de
démarrage/arrêt, redaction des secrets configurés, livraison configurée et
enfichable à l'étape terminale seulement). Vocabulaire : « phase 1 R5 »
désigne la spec `phase1-agentic`, « phase 2 R10 » la spec de la phase 2. La
revue de branche P21 est consignée plus bas, section « Revue de branche P21
(porte de la phase 2) ».

### Allowlist de la spec phase 2 : sort de chaque test

Les 8 entrées de l'allowlist de la spec `phase2-audio-stream-interaction`
(section « Test failure allowlist ») portaient les listes de la phase 1 —
8 manifests livrés, six modules activés sur le PC, quatre actions fournies.
Chacune est **réécrite en place** (même nom, docstring citant l'exigence de
remplacement) : la garantie reste la même — le profil active exactement sa
liste, n'accorde exactement que son ensemble d'actions fournies, l'installation
propre découvre exactement les manifests livrés — sur les listes de la phase 2.
Aucune entrée n'a été neutralisée par `skip`, `xfail` ou affaiblissement
d'assertion.

Les entrées 9 à 12 ont été ajoutées à l'allowlist par la réconciliation v1.3
de la spec (P22F2, constat F5 de la porte 1) : ces changements avaient été
faits par les étapes citées sans que l'allowlist v1.2 les nomme (constat G2 de
P21 ci-dessous). Trois sont réécrits en place ; le douzième est remplacé par un
test qui garde toutes ses assertions et y ajoute les liaisons de la phase 2.

| # | Test allowlisté (phase 1) | Sort | Ce qui remplace l'assertion de la phase 1 | Exigence(s) phase 2 | Commit |
| --- | --- | --- | --- | --- | --- |
| 1 | `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Réécrit en place | 11 manifests livrés (`audio_output`, `audio_input`, `stream_control` ajoutés) et les déclarations de la phase 2 dans `EXPECTED_MANIFESTS`, au lieu de 8 | R9, AC32, AC33 | `23e6e91` (P19) |
| 2 | `tests/test_examples.py::test_profile_enables_its_modules_and_contains_no_literal_credentials[pc]` | Réécrit en place | Les neuf modules du profil PC dans l'ordre de R9, au lieu des six de la phase 1 ; 0 secret littéral inchangé | R9, AC32 | `23e6e91` (P19) |
| 3 | `tests/test_examples.py::test_profile_enables_its_modules_and_contains_no_literal_credentials[server]` | Réécrit en place | `stream_control` ajouté avant `brain` au profil serveur | R9, AC32 | `23e6e91` (P19) |
| 4 | `tests/test_examples.py::test_profile_enables_its_modules_and_contains_no_literal_credentials[agent]` | Réécrit en place | Cinq modules (`capture`, `audio_input`, `audio_output`, `stream_control`, `agent_link`) au lieu de `[capture, agent_link]` | R9, AC32 | `23e6e91` (P19) |
| 5 | `tests/test_examples.py::test_profile_grants_exactly_its_provided_action_set[pc]` | Réécrit en place | Les neuf actions fournies du profil PC au lieu de quatre | R9, AC32 | `23e6e91` (P19) |
| 6 | `tests/test_examples.py::test_profile_grants_exactly_its_provided_action_set[server]` | Réécrit en place | Les neuf actions fournies du serveur, dont cinq par l'allowlist `actions` du proxy | R9, AC32 | `23e6e91` (P19) |
| 7 | `tests/test_examples.py::test_profile_grants_exactly_its_provided_action_set[agent]` | Réécrit en place | Les cinq actions fournies de l'agent au lieu de `{screen.capture}` | R9, AC32 | `23e6e91` (P19) |
| 8 | `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Réécrit en place | `len(manifests) == 11` au lieu de 8 | R9, AC33 | `23e6e91` (P19) |
| 9 | `tests/test_brain.py::test_ac10_capabilities_required_needs_structured_output_and_only_known_names` | Réécrit en place | Le nom inconnu devient `smell` et la liste connue nomme `audio, structured_output, vision` ; les deux refus restent assertés | R4, AC15 | `ee9dc94` (P6) |
| 10 | `tests/test_observations.py::test_probe_tool_and_reasons` | Réécrit en place | `PROBE_REASONS` gagne `audio_rejected` (troisième sonde, audio) | R4 | `25c152e` (P1) |
| 11 | `tests/test_observations.py::test_image_ref_fields_are_the_seven_of_r4` | Réécrit en place | `PART_TYPES` gagne `audio_ref` ; champs et types de contenu de `image_ref` inchangés | R4 | `25c152e` (P1) |
| 12 | `tests/test_profiles.py::test_server_profile_binds_screen_capture_to_the_proxy_provider` | Remplacé | `tests/test_profiles.py::test_server_profile_binds_the_device_actions_to_the_proxy_and_polls_locally` : toutes les assertions du test remplacé, plus les actions de périphérique servies par le proxy et les sondages locaux | R9, AC32 | `23e6e91` (P19) |

### Règle de l'exécuteur : un enregistrement d'interruption tardif fait foi (R10)

En phase 1, `core/actions.py::_run_invocation` écartait **tout** enregistrement
du fournisseur horodaté (`provider_completed_at`) à `expires_at` ou après, et
publiait son propre enregistrement générique (`timeout`, code `timed_out`,
message « … timed out with emission … », sans `cause`, `played_ms` ni code du
module). La phase 2 R10 change exactement ceci (`009dd89`, P3) :

- un **enregistrement d'interruption écrit par le fournisseur** — statut
  `timeout`, `cancelled` ou `error`, avec sa `cause`, son `played_ms` ou son
  propre code — horodaté à l'échéance ou après est **adopté** par le chemin
  ordinaire de validation : parties validées (R4), règle d'émission appliquée
  sans changement (un `timeout` d'écriture émise devient `external_unknown`),
  rien d'autre réécrit ; cela vaut aussi quand le minuteur de l'exécuteur a
  gagné et que le fournisseur annulé répond dans `_cancel_grace` ;
- **une confirmation tardive n'est toujours jamais un succès** : un `success`,
  un `refused` ou tout enregistrement qui n'est pas une interruption, horodaté
  à `expires_at` ou après, est remplacé par l'enregistrement générique ; la
  frontière reste `>=` sur l'horodatage du fournisseur ;
- **le minuteur de l'exécuteur reste en vigueur** : un fournisseur qui ne
  répond jamais est annulé à l'échéance et l'appel se termine par
  l'enregistrement générique, `COUNTER_ACTION_TIMEOUTS` incrémenté.

Les deux tests de frontière de la phase 1,
`tests/test_actions.py::test_a_confirmation_landing_after_the_deadline_is_never_a_success`
et `tests/test_actions.py::test_a_confirmation_landing_in_time_survives_a_late_adoption`,
restent **inchangés** (AC40). Les deux côtés de la nouvelle règle sont épinglés
à côté d'eux : `tests/test_actions.py::test_a_late_provider_timeout_is_adopted_verbatim`
(l'interruption tardive est adoptée, à `== expires_at` et `> expires_at`) et
`tests/test_actions.py::test_a_late_success_is_still_replaced_by_the_generic_record`
(la confirmation tardive ne l'est pas), complétés par
`test_a_late_refusal_is_still_replaced_by_the_generic_record`,
`test_a_late_timeout_after_emission_ends_external_unknown_as_in_time` et
`test_a_provider_that_never_answers_is_cut_by_the_timer_at_expiry`.

### Lecture (`audio.speak`, `audio.play`)

Le lecteur configuré reçoit le WAV complet (en-tête et bloc de données) sur
son entrée standard. Le succès est la lecture terminée : tous les octets
acceptés, puis sortie 0. `played_ms` est une **estimation par octets
acceptés** : la durée des octets de données que le tube du lecteur a acceptés
(l'en-tête ne compte pas), pas une horloge du périphérique — des octets
acceptés par le tube peuvent ne pas encore avoir atteint la sortie audio.

L'échéance de l'appel est `expiry = min(call.deadline, entrée + timeout_seconds)`,
la même arithmétique que l'exécuteur, et rien n'en est soustrait. Le lecteur
est arrêté **à** l'échéance de l'appel, pas avant : à `expiry`, le module
termine le lecteur et rend `timeout` avec la cause `playback` et le
`played_ms` atteint, que l'exécuteur adopte (R10). `stop_grace_seconds` court
**après** cet arrêt (terminaison, puis kill) et ne raccourcit pas l'échéance.
Un lecteur qui finit à `expiry − 0,05 s` réussit (AC38).

### Capture (`audio.capture`)

Le recorder configuré est démarré à l'appel et terminé à `t0 + seconds` ; le
segment est tronqué à `seconds` et stocké en pièce jointe louée au run. Le
recorder est tué **à** l'échéance de l'appel `expiry`, pas avant : un recorder
qui n'a rien émis quand l'échéance arrive est tué à cet instant et l'appel se
termine `error capture_timed_out`, adopté par l'exécuteur (R10, AC39).
Exactement deux quantités sont soustraites de l'échéance, toutes deux
autorisées par AC41 et limitées à leur objet : `grace_seconds`, la réserve
d'admission de `capture_too_long`, évaluée une fois avant tout démarrage
(elle refuse une capture qui ne pourrait pas finir et ne déplace pas le kill) ;
et la réserve de transcription de 1 s (`skipped:deadline`), qui borne la seule
requête de transcription optionnelle après stockage : le module décide et rend
l'enregistrement `success` porteur de l'`audio_ref` au plus tard à cette
borne, ce qui atténue — sans le prouver — le risque qu'il soit horodaté à
l'échéance ou après (voir « Réveil bloqué pendant la transcription » dans les
limites déclarées). Aucune
autre action — ni la lecture, ni les scènes, ni les sondages — ne soustrait
quoi que ce soit de son échéance.

### Fournisseur de scènes : protocole filaire

Le type `websocket` de `stream_control` parle **obs-websocket 5** (RPC
version 1) : poignée de main `Hello` → `Identify` → `Identified` avec
`rpcVersion: 1` et `eventSubscriptions: 0` (la chaîne d'authentification est
dérivée du mot de passe, du sel et du défi ; le mot de passe n'est jamais
envoyé), puis trois paires `Request`/`RequestResponse` appariées par
`requestId` : `GetSceneList` (sonde de `prepare`), `GetCurrentProgramScene`
(scène précédente et relecture) et `SetCurrentProgramScene`. Une URL `ws://`
n'est acceptée que sur loopback ; hors loopback, `wss://`. Une perte de socket
retire la disponibilité et démarre une reconnexion à délai aléatoire borné.

### Limites déclarées de la phase 2

- **Disponibilité distante** (décision 11) : la vue prête du brain liste une
  action servie par le proxy comme prête tant qu'un agent qui l'a déclarée
  est appairé ; si le module n'est pas prêt côté agent, l'appel reçoit
  `refused provider_not_ready` de l'exécuteur de l'agent. La propagation des
  changements de disponibilité de l'agent sur le fil est différée.
- **Annulation externe de l'exécuteur** (décision 1) : la branche
  `except CancelledError` de `_run_invocation` n'est pas modifiée. L'arrêt
  coordonné atteint une lecture ou une capture en cours par le hook `drain()`
  du module, qui arrête le périphérique et rend `cancelled` par le chemin
  ordinaire du fournisseur ; une annulation de l'appel exécuteur venue
  d'ailleurs garde la comptabilité de la phase 1.
- **Réveil bloqué pendant la transcription** (décision 1, AC42) :
  ce que le **module** garantit, et que les tests épinglent : il n'attend rien
  de programmé à la borne `expiry − 1 s` ou après, décide sur l'horloge lue à
  son réveil, puis écrit et rend l'enregistrement `success` sans autre attente,
  à cette borne ou avant quand son réveil arrive à l'heure
  (`tests/test_audio_input.py::test_the_window_edge_abandons_the_request_and_keeps_the_capture`,
  `test_a_delayed_wake_does_not_adopt_a_late_text`,
  `test_a_delayed_wake_adopted_late_by_the_executor_is_still_a_success`).
  La **limite déclarée** : l'horodatage de fin n'appartient pas au module mais
  à l'exécuteur (`core/actions.py::_observe`), qui le prend après le retour du
  fournisseur. Le délai réel entre la borne et cet horodatage est le retard du
  réveil **plus** le temps d'exécution et de préemption écoulé avant
  l'horodatage (écriture de l'enregistrement, ordonnancement de la boucle,
  processus suspendu). La réserve de 1 s est donc l'**atténuation, pas une
  preuve** : elle borne le risque sans garantir l'issue. Dès que ce délai
  cumulé atteint **toute la réserve** — un réveil retardé d'une seconde, ou un
  réveil moins retardé suivi d'une préemption qui porte l'horodatage à
  `expiry` ou après — l'enregistrement est horodaté à l'échéance ou après, et
  la règle de la phase 1 le traite alors en `success` tardif : non adopté,
  remplacé par l'enregistrement générique (`timeout`, `timed_out`). Aucun test
  ne peut l'exclure sur l'horloge réelle ; ce cas n'est ni converti en succès
  tardif ni compensé par une autre soustraction de l'échéance.

## Essais d'intégration par fournisseur (phase 2)

Un essai par fournisseur réel, exécuté à la main sur la machine de référence
le 23 septembre 2026 avec le lanceur opt-in `tests/test_phase2_trials.py`
(`.venv/bin/python -m pytest tests/test_phase2_trials.py -q -s -p no:cacheprovider`,
chaque essai activé par sa variable `PHASE2_*`, sauté sinon en nommant la
variable). Chaque essai active le vrai module avec ses vrais transports et
exécuteurs de processus, sur l'horloge réelle, et passe par l'exécuteur réel
sous une autorisation explicite ; le résultat cité est la ligne
`PHASE2-TRIAL …` qu'il imprime. Les formes de réglages ne contiennent ni
secret ni jeton : un secret y apparaît comme `<…>` ou `${…}`. AC34 est vérifié
par `tests/test_hygiene.py`.

| Fournisseur | Commit | Forme des réglages | Résultat | Limites | Date |
| --- | --- | --- | --- | --- | --- |
| Synthèse vocale | `23e6e91` (P19 : modules exercés), lanceur de P20 ; **non réexécuté à la porte P21** (`26abd26`) : l'endpoint et le modèle du service local ne font pas partie de la configuration versionnée, l'essai attend l'opérateur | `audio_output` : `synthesis {endpoint: http://127.0.0.1:<port>/v1/audio/speech (service local), model: <identifiant listé par le service>, api_key: vide, probe: true puis false}`, `voices {allowed: [default], default: default}`, `outputs.trial.player.argv` = interpréteur lisant stdin jusqu'au bout (puits, aucun périphérique) | Exécuté, **échec explicite** : sonde active, `PHASE2-TRIAL speech synthesis: status=error code=no_provider bound=False player=sink` (la sonde de `prepare` refuse la réponse, `audio.speak` n'est pas lié, 0 lecteur démarré) ; sonde coupée (`PHASE2_SPEECH_PROBE=0`), `PHASE2-TRIAL speech synthesis: status=error code=invalid_audio bound=True player=sink` : le service répond 200 `audio/mpeg` (11 232 octets pour « ready ») malgré `response_format: "wav"`, et le module refuse un corps qui n'est pas RIFF/WAVE PCM | Aucune synthèse lue de bout en bout : le seul service de la machine ne rend pas de WAV ; un endpoint qui honore `wav` reste à essayer ; une phrase courte, latence non mesurée | 2026-09-23 |
| Transcription | `23e6e91` (P19 : modules exercés), lanceur de P20 ; **non réexécuté à la porte P21** (`26abd26`) : l'endpoint et le modèle du service local ne font pas partie de la configuration versionnée, l'essai attend l'opérateur | `audio_input` : `sources.trial {kind: file, path: <WAV silencieux 2 s généré, 16 kHz mono 16 bits>}`, `transcription {enabled: true, endpoint: http://127.0.0.1:<port>/v1/audio/transcriptions (service local), model: <identifiant listé par le service>, language: fr, api_key: vide}` | Exécuté : sonde de `prepare` réussie (transcription non dégradée), puis `PHASE2-TRIAL transcription: status=success source=file transcription_status=ok text_chars=34` — capture `success` avec son `audio_ref` et une transcription de 34 caractères | Entrée silencieuse et pourtant 34 caractères rendus : le service invente du texte sur du silence (contenu non vérifié ni consigné) ; pas de parole réelle ; latence et fenêtre de transcription réelle non mesurées | 2026-09-23 |
| Commande de lecture | `26abd26` (arbre de code de la porte P21, réexécuté le 2026-09-23 : même ligne `PHASE2-TRIAL`) ; premier essai sur `23e6e91` (P19), lanceur de P20 | `audio_output` : `outputs.trial.player.argv: [aplay, -q, -]`, `clips.trial.path: <WAV silencieux 1 s généré, 16 kHz mono 16 bits>`, `synthesis.endpoint` vide (`audio.speak` non lié) | Exécuté : `PHASE2-TRIAL playback command: status=success duration_ms=1000 played_ms=1000` | Silence : rien d'audible vérifié ; `played_ms` est l'estimation par octets acceptés ; l'arrêt à l'échéance n'a pas été provoqué sur le périphérique réel (couvert par AC38 avec le runner injecté) ; une seule sortie, périphérique ALSA par défaut | 2026-09-23 |
| Commande de capture | `26abd26` (arbre de code de la porte P21, réexécuté le 2026-09-23 : même ligne `PHASE2-TRIAL`) ; premier essai sur `23e6e91` (P19), lanceur de P20 | `audio_input` : `sources.trial {kind: command, argv: [arecord, -q, -f, S16_LE, -r, 16000, -c, 1, -t, wav, -]}`, `default_source: trial`, transcription désactivée ; appel `{seconds: 2}` | Exécuté : `PHASE2-TRIAL capture command: status=success size=61426 duration_ms=1918 sample_rate_hz=16000 channels=1 audio_ref=True` | 1 918 ms au lieu de 2 000 : le recorder est terminé à `t0 + 2 s` et son démarrage consomme le reste, le segment garde ce qu'il a émis ; contenu du micro non vérifié ; `capture_timed_out` non provoqué sur le périphérique réel (couvert par AC39) ; périphérique ALSA par défaut | 2026-09-23 |
| Fournisseur de scènes | `23e6e91` (P19 : modules exercés), lanceur de P20 ; **non réexécuté à la porte P21** (`26abd26`) : l'essai bascule la scène de programme du logiciel de diffusion en service, ce que la porte ne fait pas sans l'accord de l'opérateur | `stream_control` : `scenes.provider {kind: websocket, url: ws://127.0.0.1:4455, password: ${OBS_WEBSOCKET_PASSWORD}}`, `allowed: [Scène, Trial phase2]`, délais de connexion et de requête par défaut (5 s), sondages désactivés ; serveur obs-websocket 5 du logiciel de diffusion installé, authentification exigée | Exécuté, deux passes, chacune une vraie bascule confirmée par relecture : `PHASE2-TRIAL scene provider (restore): status=success scene=Scène previous_scene=Trial phase2 reconciled=False`, puis `PHASE2-TRIAL scene provider: status=success ready=True scene=Scène previous_scene=Scène reconciled=False` et `PHASE2-TRIAL scene provider (restore): status=success scene=Trial phase2 previous_scene=Scène reconciled=False` ; le logiciel finit sur sa scène de départ | Loopback seulement, pas de `wss://` ; perte de socket, reconnexion et réconciliation non provoquées contre le serveur réel (couvertes par le pair en mémoire) ; aucune mesure de latence | 2026-09-23 |
| Sondages de plateforme | `23e6e91` (P19 : modules exercés), lanceur de P20 ; **non réexécuté à la porte P21** (`26abd26`) : toujours aucun identifiant de plateforme | `twitch {client_id, client_secret, access_token, broadcaster_id, bot_user_id: ${TWITCH_*}}` (jeton avec la portée de gestion des sondages), `stream_control {scenes.provider.kind: none, polls.enabled: true}` ; appel `{question, options: [2], duration_seconds: 15}` sur la chaîne du diffuseur | **Non exécuté** : aucun identifiant de plateforme sur la machine de référence (`PHASE2_POLL_PLATFORM` et les variables `TWITCH_*` absentes) ; créer un sondage exige un jeton portant la portée des sondages sur une chaîne affiliée ou partenaire | Le service de sondage n'est éprouvé que contre le double scripté et le transport HTTP simulé (AC26–AC28) ; aucune requête réelle vers la plateforme | 2026-09-23 |

## Revue de branche P21 (porte de la phase 2)

La porte relit **l'ensemble** du diff de la phase 2 contre le point de
livraison de la phase 1 (`<base>` = `7e26038`, dernier commit de la phase 1)
jusqu'à `26abd26` (P20), l'arbre de code sur lequel chaque contrôle ci-dessous
a été exécuté ; le commit de porte ne modifie que ce document et
[campaigns/phase2/README.md](campaigns/phase2/README.md), qui porte le détail
(carte R1–R10 → tests, commandes exactes). Résultat : **aucun défaut de code
trouvé** ; quatre écarts de périmètre ou d'allowlist, enregistrés ci-dessous,
dont la correction relève de la spec et non d'un fichier que P21 a le droit de
modifier.

| # | Contrôle de la porte | Résultat |
| --- | --- | --- |
| 1 | `git diff --name-only <base>..HEAD` contre les `targets` de la spec ; `Files:` du plan ⊂ targets | 36/36 targets touchés ; 5 fichiers hors targets (G1) ; `Files:` du plan : tous targets sauf `docs/campaigns/phase2/README.md` (périmètre permis) |
| 2 | AC7 : `git diff <base>..HEAD -- modules/brain/` | Hunks R4 seulement (sonde audio, `_encode_part`, règle d'omission, estimation, libération des pièces jointes, alias `_expired_image`) ; 0 hunk dans `_resolve_delivery`, `_deliver_all`, `_loop` |
| 3 | `core/actions.py` et AC40 | Hunks dans `_reject_parts`, `_discard_images` (P2) et `_run_invocation`, `_is_interruption_record` (P3) seulement ; les deux tests de frontière de la phase 1 identiques octet pour octet, `tests/test_actions.py` sans aucune ligne retirée |
| 4 | AC41 | Grep `adoption_lead\|lead_seconds\|deadline_margin\|early_stop\|deadline_lead` : 0 ligne ; aucune clé lead/marge dans les trois schémas ; exactement deux soustractions à l'échéance, toutes deux dans `modules/audio_input/` (`capture_too_long` et la fenêtre de transcription `expiry − 1 s`) ; le kill du recorder, l'arrêt du lecteur et les attentes de `stream_control` sont à `expiry` (`max(0, expiry − now)`, durée restante, rien de soustrait) |
| 5 | Tables d'appelants | Chaque site `PART_TYPE_IMAGE_REF` hors `modules/capture` a son jumeau `audio_ref` ; le seul site `IMAGE_CONTENT_TYPES` hors contrat valide la partie `image_ref`, le proxy lit `ATTACHMENT_CONTENT_TYPES` ; les 6 constructions `RuntimeContext(` passent |
| 6 | R1–R10 → étape → test | Chaque exigence et chaque AC1–AC43 a au moins un test nommé qui passe (carte dans le README de campagne) |
| 7 | Seuls les 8 tests allowlistés changent une assertion de la phase 1 | **Non** : 4 autres tests de la phase 1 changés ou remplacés (G2) |
| 8 | Neutralité (`core/`, `modules/brain/`) | Aucun `poll`, `twitch`, `obs`, `pipewire`, `aplay`, `ffmpeg` ajouté ; `audio`/`capture` uniquement dans le vocabulaire R4 et le texte d'omission ; `poll` 0 fois dans `core/runtime.py` et `core/main.py` (AC25) ; 0 `proxy`/transport ajouté au brain (AC35) ; aucun littéral de modèle, aucune attente positive |
| 9 | Raccourcis « faux fini » | Aucun : pas de `skip`/`xfail` hors lanceur opt-in, aucune assertion affaiblie, `except … pass` limités aux signaux vers un processus déjà sorti, `NotImplementedError` limités à la base abstraite des doubles, aucune dépendance nouvelle |
| 10 | Suite, profils, topologie, essais | `1887 passed, 12 skipped` ; `--check-config` sur les trois profils vert ; `test_ac39_two_process_topology_over_loopback` identique et vert (son assistant `_agent_profile` a changé, G3) ; 2 essais réexécutés sur `26abd26`, 4 non (G4) |

| # | Constat de la porte | Fichiers | Étape d'origine | Traitement |
| --- | --- | --- | --- | --- |
| G1 | Cinq fichiers modifiés hors `targets` : `core/loader.py` (diagnostic AC25 nommant le module détenteur, lu dans le registre et jamais dans le texte de l'exception), `.gitignore` (sorties du générateur de spec de la campagne), `tests/test_brain.py`, `tests/test_integration.py` (variables d'environnement du profil PC), `tests/test_proxy_process.py` | ces cinq | P4, `aaf6983`, P6, P19, P19 | Non corrigé à P21 (fichiers hors de son périmètre) : les revertir casserait AC25, AC15 et le démarrage du profil PC ; la liste `targets` de la spec est à amender |
| G2 | Assertions de la phase 1 changées hors allowlist : `tests/test_brain.py::test_ac10_capabilities_required_needs_structured_output_and_only_known_names` (`audio` connu), `tests/test_observations.py::test_probe_tool_and_reasons` (`audio_rejected`), `tests/test_observations.py::test_image_ref_fields_are_the_seven_of_r4` (`PART_TYPES` gagne `audio_ref`), `tests/test_profiles.py::test_server_profile_binds_screen_capture_to_the_proxy_provider` remplacé par `test_server_profile_binds_the_device_actions_to_the_proxy_and_polls_locally` (sur-ensemble des assertions) | tests ci-contre | P6, P1, P1, P19 | Changements de contrat légitimes, chacun citant R4 ou R9 dans sa docstring, aucune garantie affaiblie ; non corrigé : l'allowlist de la spec est à compléter |
| G3 | AC20 « le test de topologie à deux processus passe inchangé » : la fonction de test est identique et verte, mais son assistant `_agent_profile` retire les trois modules de phase 2 du profil agent | `tests/test_proxy_process.py` | P19 | Enregistré ; le scénario de phase 2 à travers le proxy (AC35) couvre ces modules |
| G4 | Essais par fournisseur : 2 sur 6 réexécutés sur l'arbre de la porte (lecture, capture) ; synthèse et transcription attendent un endpoint d'opérateur, la scène basculerait le logiciel de diffusion en service, les sondages n'ont pas d'identifiant | ce document | P20 | Colonne « Commit » de chaque ligne mise à jour avec la raison |

G1, G2 et G3 sont réconciliés par la spec v1.3 (P22F2, constat F5 de la porte
1) : les cinq fichiers de G1 sont déclarés dans `targets` avec leur raison, les
quatre tests de G2 sont les entrées 9 à 12 de l'allowlist ci-dessus, et AC20
précise que seul le corps du test de topologie reste inchangé, son assistant
réduisant le profil agent à l'essai `screen.capture` de la phase 1 (G3). La
comparaison de `git diff --name-only 7e26038..HEAD` avec `targets`, hors
`docs/campaigns/phase2/`, ne rend plus aucun fichier.

## Phase 3 — présence, mémoire des spectateurs, modération et plateformes

La phase 3 (branche `feat(phase3)`, commits `f30f6cb` (P1) à `0c170ef` (P24),
spec `phase3-presence-memory-platforms` v1.0 archivée dans
[campaigns/phase3/spec.md](campaigns/phase3/spec.md), plan dans
[campaigns/phase3/plan.md](campaigns/phase3/plan.md), issue de
[design-v2.md](design-v2.md) §6 et de l'étude d'usage
[research/usage-createurs-assistant-ia-2026.md](research/usage-createurs-assistant-ia-2026.md))
ajoute le pack de présence (persona, routes, notifications communautaires et
le profil livré `presence.yaml.example`), les clips de diffusion
(`stream.clip.create`), la mémoire des spectateurs (`memory.recall`,
`memory.record`), la modération sous règles strictes (`moderation.request`),
la veille d'écran commandée (`watch`) et deux plateformes, Kick et YouTube.
Elle ne remplace de la phase 2 **que ce que son allowlist nomme** — six tests,
tous réécrits en place. Comme les sections précédentes, celle-ci enregistre
**ce qui change et par quoi**, pas une nouvelle conformité : les garanties des
phases 0 à 2 restent en vigueur (admission bornée, cycle de vie par phases,
statut terminal explicite, défaut-refus lectures comprises, une action par
tour, rétention bornée, délai global unique de démarrage/arrêt, redaction des
secrets déclarés, un effet externe incertain jamais mémorisé comme confirmé ni
rejoué, livraison configurée et enfichable à l'étape terminale seulement). Les
trois profils de la phase 2 ne sont pas modifiés ; aucun nom de plateforme
n'entre dans `core/`. Vocabulaire : « phase 3 R5 » désigne la spec de cette
phase. Les essais réels sont consignés plus bas, sous-section « Essais réels
par plateforme (phase 3) », produits par le lanceur opt-in
`tests/test_phase3_trials.py`.

### Versionnement de la spec phase 2 (phase 3) : sort de chaque test allowlisté

Les 6 entrées de l'allowlist de la spec `phase3-presence-memory-platforms`
(section « Test failure allowlist ») portaient les valeurs du catalogue de la
phase 2 — 11 manifests, 9 noms d'action déclarés une fois chacun, les réglages
de `twitch` et du `brain` de la phase 2. Chacune est **réécrite en place**
(même nom, docstring citant l'exigence de remplacement) : la garantie reste la
même — le catalogue découvert, les lignes de paquet et les schémas de réglages
sont exactement ceux attendus — sur les valeurs de la phase 3. Suivant la
décision 11 du plan, chaque étape qui cassait une de ces assertions l'a
réécrite à la valeur courante de son catalogue ; P23 (`87e8a98`) épingle les
valeurs finales. Aucune entrée n'a été neutralisée par `skip`, `xfail` ou
affaiblissement d'assertion.

| # | Test allowlisté (phase 2) | Sort | Ce qui remplace l'assertion de la phase 2 | Exigence(s) phase 3 | Commit |
| --- | --- | --- | --- | --- | --- |
| 1 | `tests/test_examples.py::test_manifests_are_unique_and_have_coherent_capabilities` | Réécrit en place | Exactement 17 manifests (`clips`, `viewer_memory`, `moderation`, `watch`, `kick`, `youtube` ajoutés) et 13 noms d'action ; chaque nom a un seul déclarant sauf `chat.write`, déclaré par exactement `twitch`, `kick` et `youtube` ; `twitch` gagne le réglage `notices` et le type de déclencheur `event_kind`, ses autres clés inchangées | R1, R2, R3, R4, R5, R6, R7, AC40 | `de3057e` (P7) à `87e8a98` (P23, valeurs finales) |
| 2 | `tests/test_examples.py::test_ac30_the_chat_only_profile_starts_and_runs_the_phase_1_scenario` | Réécrit en place | Le catalogue découvert déclare 13 noms d'action au lieu de 9 (`stream.clip.create`, `memory.recall`, `memory.record`, `moderation.request`), `chat.write` 3 fois et chaque autre nom une fois ; le scénario de la phase 1 est inchangé | R2, R4, R5, R7 | `f487b3a` (P9) à `87e8a98` (P23) |
| 3 | `tests/test_profiles.py::test_installed_distribution_discovers_the_shipped_manifests_and_answers_help` | Réécrit en place | L'installation propre découvre exactement 17 manifests au lieu de 11 ; `--help` inchangé | R8, AC40 | `f487b3a` (P9) à `87e8a98` (P23) |
| 4 | `tests/test_profiles.py::test_pyproject_ships_the_three_phase_2_manifests_and_no_new_dependency` | Réécrit en place | Exactement 17 lignes `modules.<name>` de données de paquet au lieu de 11 ; dépendances d'exécution toujours exactement `aiohttp` et `PyYAML` (assertion inchangée) | R8, AC40 | `f487b3a` (P9) à `87e8a98` (P23) |
| 5 | `tests/test_twitch.py::test_manifest_declares_twitch_source_and_sink` | Réécrit en place | Les propriétés de réglages gagnent l'optionnel `notices` (ses `kinds` : les six types de notification Twitch) et les types de déclencheur gagnent `event_kind` ; politique par défaut, action et identifiants inchangés | R1 | `de3057e` (P7) |
| 6 | `tests/test_brain.py::test_manifest_declares_v2_shape_settings_hook_and_no_grant` | Réécrit en place | Les propriétés de réglages sont celles de la phase 2 plus les optionnels `persona` et `routes` ; l'ensemble requis est inchangé | R1 | `2900a0b` (P5) |

### Pack de présence : configuration ou code

Le pack de présence est ce que l'étude d'usage (§6) décrit comme
« configuration, pas code ». La phase 3 le rend vrai **pour ce qui suit**, avec
deux petits leviers de code seulement : la persona et les routes du `brain`
(une route choisit, selon le type d'événement, un premier mot de commande et
une audience attestée par la plateforme, des instructions et une liste de
livraison) et les notifications communautaires des modules de plateforme
(`notices.kinds`). Tout le reste est dans `presence.yaml.example` : routes,
politiques de déclenchement et règles d'autorisation. Ce qui demande encore du
code est **nommé ici, pas caché** (décision 1 de la spec).

| Élément de présence | Configuration ou code | Ce qui le porte (ou ce qui manque) |
| --- | --- | --- |
| Accueil | Configuration — `brain` (route `thanks`) et `twitch` (`notices.kinds` : `raid`, `follow`) | L'arrivée d'un raid ou d'un suiveur déclenche un run dont le spectateur est le raider ou le suiveur. Un premier message sans mention n'a pas de déclencheur propre : la politique par défaut ne retient que les mentions |
| Remerciements | Configuration — `brain` (route `thanks`) et `twitch` (`notices.kinds` : `sub`, `resub`, `sub_gift`, `community_sub_gift`) | Politique `event_kind` sur les six types ; le spectateur remercié est l'auteur attesté de la notification |
| Résumé (« qu'ai-je manqué ? ») | Configuration — `brain` (route `missed`, commande `!missed`) et `chat_context` (`chat.read`) | Le modèle lit la transcription retenue puis résume ; bornes de `limits.chat_context` |
| Traduction | Configuration — `brain` (route `translate`, commande `!translate`) | Instructions de route seulement ; aucune action nouvelle |
| Sondages (commande de chat) | Code — `stream_control` (`stream.poll.create`) | L'action existe, mais une livraison ne passe que le texte de la réponse : une question et des options tirées d'une commande de chat demandent un module de commande qui construit les arguments |
| Scènes | Configuration — `brain` (routes `scene-soon`, `scene-brb`, `scene-end`, audience `broadcaster`) et `stream_control` (`stream.scene.set`) | Livraison à effet seul `text_argument: none` ; le démarrage refuse une route à effet ouverte à tous |
| Voix | Configuration — `audio_output` (`audio.speak`, `synthesis.endpoint`) et `brain` (entrée de livraison) | Non liée tant que `synthesis.endpoint` est vide dans le profil livré ; l'essai de la phase 2 attend toujours un endpoint rendant du WAV |
| Jingles | Configuration — `audio_output` (`audio.play`, clip `chime`) et `brain` (route `thanks`) | Joué après le message de remerciement, dans la liste de la route |
| Réactions à l'écran | Configuration — `watch` (`!watch`/`!unwatch`), `capture` (`screen.capture`) et `brain` (route `watch`, principal `brain.watch`) | Aucune capture de fond : seules les ticks d'une session commandée lancent un run |
| Clips | Configuration — `clips` (`stream.clip.create`) et `brain` (route `clip`, audience `moderators`) | Effet seul, jamais proposé par le modèle ; service `clip` publié par `twitch` seulement |
| Mémoire | Configuration — `viewer_memory` (`memory.recall` offert au modèle, `memory.record` après la livraison) | Voir « Modèle de la mémoire des spectateurs » ; `!forgetme` efface |
| Récompenses de points de chaîne | Code — `twitch` (aucun abonnement aux récompenses) | Ni ingestion ni livraison : la décision 3 de la spec exclut les rachats, et aucun type d'événement ne les porte |
| Annonce du lien du clip | Code — `clips` et `brain` | Le `clip_id` et l'`url` restent dans l'observation de `stream.clip.create` ; une entrée de livraison ne reçoit que le texte de la réponse, écrit avant le clip : annoncer le lien demande une étape qui lit le résultat |
| Remerciement d'un donateur anonyme | Code — `twitch` et `brain` | Un don anonyme entre dans la transcription sous `system:anonymous` mais n'est jamais admis (décision 3 du plan) : aucun run ne le remercie |

### Matrice des capacités par plateforme

Une plateforme ne publie un service ou ne déclare une action que là où son
API publique offre l'opération (décision 7 de la spec) ; la vue des capacités
montre le reste non lié avec une raison nommée.

| Capacité | Twitch | Kick | YouTube |
| --- | --- | --- | --- |
| Réception du chat | EventSub (WebSocket) `channel.chat.message` | Webhooks signés RSA-SHA256 sur un écouteur local ; la plateforme doit l'atteindre en **HTTPS public** (reverse proxy ou tunnel), prérequis de déploiement documenté, non fourni ; corps ≤ 65 536 octets, horodatage à ±300 s, doublons refusés | Scrutation du chat du direct actif à `max(intervalle du serveur, min_poll_interval_seconds)` (défaut 5 s, 1–60) |
| `chat.write` | Oui | Oui, ≤ 500 caractères (`text_too_long` au-delà, 0 requête) | Oui, ≤ **200 caractères** (`text_too_long` au-delà, 0 requête) |
| Notifications (`notices.kinds`) | `sub`, `resub`, `sub_gift`, `community_sub_gift`, `raid`, `follow` | `follow`, `sub`, `resub`, `sub_gift` | `sub` (membre), `resub` (palier), `sub_gift`, `tip` (Super Chat, Super Sticker) |
| Clips (`stream.clip.create`) | Oui (service `clip`) | **Non** : aucune API de clip ; non lié, `platform_unsupported` | **Non** : aucune API de clip ; non lié, `platform_unsupported` |
| Sondages (`stream.poll.create`) | Oui (service `poll`, phase 2) | **Non** : aucune API de sondage ; non lié, `platform_unsupported` | **Non** : aucune API de sondage ; non lié, `platform_unsupported` |
| Modération (service `moderation`) | `delete_message`, `timeout` (en secondes, sans arrondi) | **`timeout` seulement**, durée arrondie **à la minute supérieure**, 1–10 080 minutes | `delete_message`, `timeout` (bannissement `temporary`) |
| Quota | Aucun registre local ; un refus de débit de la plateforme est `rate_limited` (clips) ou `rate_limited_platform` (modération), jamais rejoué | Limites de débit (`rate_limited`, blocage jusqu'à l'instant annoncé) | **Quota quotidien local** : `quota.daily_units` (défaut 10 000), `quota.write_reserve_units` (défaut 1 000) réservées aux envois, coûts par requête (`list` 5, `insert` 50, `delete` 50, `ban` 50, `broadcast_lookup` 1), remise à zéro à 00:00 America/Los_Angeles ; une lecture qui entamerait la réserve n'est pas émise (`quota_exhausted`), un envoi sans unités est refusé avec 0 requête |
| Authentification | Jeton d'accès configuré | `client_secret`, `access_token` ; clé publique configurée ou lue une fois au `prepare` | Jeton de rafraîchissement échangé avant expiration ; un refus dégrade le module (`auth_refresh_failed`) |

**Portées Twitch** (P7, P8), à porter par le jeton du compte bot ; une portée
manquante n'apparaît qu'à l'appel, en `platform_rejected` (clips, modération)
ou en dégradation du seul `follow` :

- lecture et écriture du chat : les portées de la phase 1 ;
- `follow` : la portée de lecture des suiveurs côté modérateur
  (`moderator:read:followers`) ; un abonnement refusé dégrade les seules
  notifications `follow`, le chat continue ;
- clips : `clips:edit` ;
- modération : `moderator:manage:chat_messages` (suppression) et
  `moderator:manage:banned_users` (exclusion temporaire) ; le point d'accès
  des exclusions n'est jamais appelé sans durée, donc aucun bannissement
  permanent ;
- sondages (phase 2) : la portée de gestion des sondages.

### Modèle de la mémoire des spectateurs

- **Format.** Un fichier JSON UTF-8 par `(platform, channel_id, viewer_id)`
  dans `directory`, nommé par le SHA-256 hexadécimal de la clé sérialisée en
  tableau JSON compact, plus `.json` : aucun identifiant n'apparaît dans un
  nom. Contenu : `format: 1`, les trois champs de la clé, `display_name`
  (≤ 64 caractères), `first_seen`, `last_seen`, `last_used` (UTC, format fixe),
  `use_count`, `interactions` et `notes` (chacune `at`, `viewer_text`
  ≤ 200 caractères, `reply_text` optionnel ≤ 200, `delivery` : `confirmed`,
  `unconfirmed` ou `none`). Écriture atomique : fichier temporaire du même
  répertoire, `fsync`, puis `os.replace`.
- **Bornes** (défauts du profil livré) : `retention_days` 30 (1–365),
  `max_files` 1000, `max_total_bytes` 16 777 216, `max_file_bytes` 4096
  (512–65 536), `max_notes` 10, `max_recall_bytes` 1024 (512–8192). Une note
  qui dépasserait `max_notes` ou `max_file_bytes` fait tomber les plus
  anciennes notes du fichier d'abord. **Règle de la note seule** (P10) : si la
  note la plus récente, seule, ne tient toujours pas dans `max_file_bytes`, le
  fichier garde **0 note** ; si les métadonnées seules ne tiennent pas,
  `display_name` est raccourci ; la borne du fichier est absolue.
- **Ordre d'éviction.** Avant une écriture qui ferait dépasser `max_files` ou
  `max_total_bytes`, les candidats — tous les fichiers de mémoire sauf celui
  qu'on écrit — sont supprimés dans l'ordre croissant de (`last_used`,
  `use_count`, `first_seen`, nom de fichier) jusqu'à ce que les deux bornes
  tiennent après l'écriture. La rétention supprime au `prepare` et à chaque
  balayage (`sweep_interval_seconds`) un fichier dont `last_seen` dépasse
  `retention_days` ; un tel fichier n'est jamais rendu par un rappel. Au
  `prepare`, les fichiers mal formés ou trop gros sont supprimés, les noms
  étrangers ne sont ni lus ni comptés ni supprimés.
- **Suppression refusée.** Un fichier dont la suppression échoue (répertoire
  lisible mais non modifiable, par exemple) reste compté dans les deux bornes
  et est retenté à chaque passe d'éviction et à chaque balayage. Si les
  bornes ne tiennent toujours pas, l'écriture est refusée et, au `prepare`, le
  module échoue sans se déclarer prêt, après un `module.degraded` sans valeur
  (« the memory bounds cannot be met: a deletion failed ») ; un fichier
  resté sur le disque dans les bornes donne un `module.degraded` (« a memory
  file could not be deleted »), et un effacement qui échoue est une erreur,
  jamais un fichier absent (porte 1, F3, corrigé en P27F2).
- **Chemins d'effacement.** (a) Un message de chat dont le texte est
  exactement `!forgetme` (`forget_command`, vide = désactivé) efface le
  fichier de son auteur attesté pour cette plateforme et cette chaîne, quelle
  que soit la décision du déclencheur ; une notification portant ce texte
  n'efface rien. (b) La commande hors ligne, qui n'ouvre aucun transport
  réseau : `python -m modules.viewer_memory forget --config <fichier>
  --platform <p> --channel <c> --viewer <v>`, `forget-all --config <fichier>`
  et `stats --config <fichier>` (nombre et octets). Chaque éviction,
  expiration et effacement publie un fait `memory.removed` (raison
  `evicted`, `expired`, `corrupt` ou `erased`, et le compte), sans
  identifiant de spectateur.
- **Dans les runs.** Le modèle peut appeler `memory.recall` (lecture, sans
  argument : le spectateur est celui de la session) ; `memory.record` n'est
  jamais offert au modèle — le `brain` l'appelle une fois après la livraison
  terminale, avec `delivery: confirmed` seulement si toutes les livraisons
  requises ont réussi, `unconfirmed` sans texte de réponse après un
  `external_unknown`, `none` sinon. Un run de veille (`system:watch`) reçoit
  `error no_viewer`.
- **Limites honnêtes.**
  - Les fichiers sont des **fichiers locaux en clair, non chiffrés** : qui
    lit le répertoire lit les notes.
  - Les identités sont **par plateforme et par chaîne, jamais fusionnées** :
    le même spectateur sur Twitch et sur YouTube, ou sur deux chaînes, a deux
    fichiers et deux mémoires.
  - **Un arrêt brutal peut perdre la dernière écriture** : l'écriture
    atomique garantit l'ancien fichier ou le nouveau, jamais un fichier
    partiel, mais une note non encore remplacée sur disque est perdue.
  - La règle de la note seule ci-dessus : sous un petit `max_file_bytes`, un
    échange peut ne laisser aucune note.

### Modération

`moderation.request` est la seule écriture que le modèle peut proposer
(`model_proposable: true`) : le `brain` ne l'offre que si une règle
l'accorde, l'exécuteur revérifie chaque appel, et une deuxième demande dans le
même run est `refused run_limit`. Arguments : `operation`
(`delete_message` ou `timeout`), `message_id`, `reason` (≤ 200 caractères),
`duration_seconds` pour `timeout` seulement. **Aucune opération ne bannit
définitivement.**

- **Modes** (`mode`, remplaçable par `channels.<platform>/<channel_id>.mode`) :
  - **`alert` — le défaut livré** : la demande est enregistrée
    (disposition `alerted`), 0 requête à la plateforme ;
  - `propose` : une proposition est stockée (disposition `proposed`,
    `proposal_id`, au plus `propose.max_pending` 32, durée
    `propose.proposal_ttl_seconds` 300), 0 requête ; elle est appliquée par `!modok <id>` ou écartée par
    `!modno <id>`, écrits dans la même chaîne par le diffuseur ou un
    modérateur attestés, ou appliquée aussitôt si son opération figure dans
    `propose.auto_apply` (vide par défaut) ; les propositions en attente ne
    survivent pas à un redémarrage ;
  - `act` : appliquée aussitôt.
- **Règles strictes**, vérifiées dans cet ordre à chaque application (act,
  approbation, application automatique), chacune refusant avec son code et
  0 requête : `operation_not_allowed` (hors `act.operations`, défaut
  `[delete_message]`), `target_unknown`, `target_protected` (auteur
  diffuseur, modérateur ou VIP attesté, ou le compagnon lui-même — non
  configurable), `duration_out_of_range` (hors 1..`max_timeout_seconds`,
  défaut 300, après l'arrondi de la plateforme), `rate_limited`
  (`max_actions_per_window` 3 par `window_seconds` 600 dans la chaîne),
  `target_cooldown` (même **auteur** dans `per_target_cooldown_seconds` 600),
  `platform_unsupported` (Kick n'offre pas `delete_message`). Une application
  admise envoie exactement une requête, jamais rejouée : `applied`,
  `platform_rejected`, `rate_limited_platform` ou `external_unknown`.
- **Limite de l'index** (P14) : `target_unknown` et `target_protected` lisent
  un index propre au module, `message_id → (auteur, rôles attestés)`, des
  seuls événements de type `message`, borné par les limites de
  `limits.chat_context` (`max_messages` par chaîne, 256 sans elles). Un
  message sorti de l'index mais encore dans la transcription est
  `target_unknown` — choix conservateur ; une notification n'est jamais une
  cible.
- **Aucune action silencieuse** : chaque demande, approbation, rejet et
  expiration publie exactement un fait `moderation.decision`, que l'audit
  enregistre.

### Déclencheur de veille (`watch`)

La veille est une **entrée**, pas une capture de fond. Le module `watch`
(activé seulement dans `presence.yaml.example`, jamais dans les profils de la
phase 2) émet des événements `watch_tick` pour au plus 4 chaînes, et
seulement pendant une **session** :

- **Démarrage** : un message exactement `!watch` (`start_command`) d'un
  auteur attesté de `command_audience` (`broadcaster` par défaut, ou
  `moderators`) dans la chaîne, ou le démarrage si `activation: startup` est
  configuré explicitement (défaut `activation: command`).
- **Arrêt** : `!unwatch` de la même audience, `max_active_seconds` (défaut
  3600, 60–14 400) ou l'arrêt du runtime ; chaque changement publie un fait
  `watch.state` (`active`/`inactive`, raison `command`, `startup`,
  `max_active` ou `shutdown`).
- **Cadence et plafonds** : une tick toutes les `interval_seconds` (défaut 60,
  15–3600) sur l'horloge injectée, au plus `max_ticks_per_hour` (défaut 30,
  1–240 et ≤ 3600 / `interval_seconds`) par chaîne sur toute heure glissante ;
  une tick due pendant qu'un run de veille de la chaîne est en file ou en
  cours est sautée et comptée — au plus un run de veille par chaîne en vol.
- **Chemin** : la tick (texte `prompt_text`, auteur réservé `system:watch`,
  qu'aucun spectateur ne peut porter : chaque plateforme refuse un
  identifiant contenant `:`) passe par le moteur de déclencheurs (politique
  par défaut `event_kind: [watch_tick]`) et l'admission ; elle n'entre ni dans
  la transcription ni dans l'annuaire. Chaque appel de son run est fait sous
  le principal **`brain.watch`** : une règle doit nommer ce principal pour
  chaque action (dans le profil livré : `screen.capture` et `chat.write`
  seulement). `screen.capture` est servi par le même fournisseur qu'une
  capture à la demande, y compris à travers le proxy.

### Limites déclarées de la phase 3

- **Sondages hors Twitch** : avec Twitch, Kick et YouTube activés,
  `stream.poll.create` n'est lié que pour Twitch ; `stream_control` nomme
  Kick et YouTube non liés avec la raison `platform_unsupported` (vue
  `unbound`, un `module.degraded` sans valeur qui liste ces plateformes), et un
  appel sur Kick ou YouTube se termine `error no_provider` avec 0 requête
  (porte 1, F2, corrigé en P27F2).
- **Joignabilité de Kick** : l'écouteur des webhooks est local ; l'exposer en
  HTTPS public est à la charge du déploiement.
- **Quota YouTube** : le registre est local et remis à zéro à minuit
  America/Los_Angeles ; une réponse `quotaExceeded` de la plateforme le
  ramène à 0.
- **Propositions de modération** non persistées ; **mémoire** en clair (voir
  plus haut).

### Essais réels par plateforme (phase 3)

Un essai par ligne de R8, lancé à la main sur la machine de référence le
27 septembre 2026 avec le lanceur opt-in `tests/test_phase3_trials.py`
(`.venv/bin/python -m pytest tests/test_phase3_trials.py -q -s -p no:cacheprovider`,
chaque essai activé par ses variables, sauté sinon en les nommant). Un essai
active les vrais modules par le vrai chargeur, sur l'horloge réelle et leurs
vrais transports, sous une autorisation explicite ; le résultat cité est la
ligne `PHASE3-TRIAL …` qu'il imprime. Les formes de réglages ne contiennent
aucun secret : un identifiant y apparaît comme `${…}`. Le commit est l'arbre
sur lequel l'essai a tourné (ou aurait tourné). AC41 est vérifié par
`tests/test_hygiene.py`.

| Essai | Commit | Forme des réglages | Résultat | Limites | Date |
| --- | --- | --- | --- | --- | --- |
| clips de plateforme | `0c170ef` (P24) | `twitch {client_id: ${TWITCH_CLIENT_ID}, client_secret: ${TWITCH_CLIENT_SECRET}, access_token: ${TWITCH_ACCESS_TOKEN}, broadcaster_id: ${TWITCH_BROADCASTER_ID}, bot_user_id: ${TWITCH_BOT_USER_ID}}` (jeton `clips:edit`), `clips {min_interval_seconds: 30}` ; un `stream.clip.create` sur la chaîne en direct | **Non exécuté** — aucun identifiant Twitch sur la machine de référence (`PHASE3_TRIAL_CLIPS` et les variables `TWITCH_*` absentes) ; l'essai exige aussi une chaîne en direct | Le service `clip` n'est éprouvé que contre le transport HTTP scripté et le double de clip (AC8–AC12) ; aucune requête réelle | 2026-09-27 |
| modération de plateforme | `0c170ef` (P24) | `twitch {…: ${TWITCH_*}}` (jeton `moderator:manage:chat_messages`), `moderation {mode: act, act.operations: [delete_message]}` ; message de test `${PHASE3_MODERATION_MESSAGE_TEXT}` d'un compte non modérateur | **Non exécuté** — aucun identifiant Twitch ni compte de test (`PHASE3_MODERATION_MESSAGE_TEXT` et `TWITCH_*` absentes) | Suppression et exclusion temporaire éprouvées contre le transport scripté seulement (AC24–AC28) | 2026-09-27 |
| notifications communautaires | `0c170ef` (P24) | `twitch {…: ${TWITCH_*}, notices.kinds: [raid, follow]}` (jeton avec la lecture des suiveurs côté modérateur) ; première notification `raid` ou `follow` de la chaîne | **Non exécuté** — aucun identifiant Twitch (`PHASE3_TRIAL_NOTICES` et `TWITCH_*` absentes) | Les noms de champs EventSub des notifications sont épinglés sur des charges copiées de la référence publique, non confrontés à la plateforme | 2026-09-27 |
| Kick | `0c170ef` (P24) | `kick {client_secret: ${KICK_CLIENT_SECRET}, access_token: ${KICK_ACCESS_TOKEN}, channels: [${KICK_CHANNEL_ID}], listener {host: 127.0.0.1, port: ${PHASE3_KICK_LISTENER_PORT}}, public_key: ${KICK_PUBLIC_KEY} ou lue au prepare}` ; un webhook signé puis un `chat.write` | **Non exécuté** — aucun identifiant Kick (`PHASE3_KICK_LISTENER_PORT` et `KICK_*` absentes) et aucun point d'entrée HTTPS public vers l'écouteur local | Signature, horodatage, taille et doublons éprouvés avec une paire de clés de test et l'émetteur signé (AC34, AC35) ; forme réelle des en-têtes non confrontée | 2026-09-27 |
| YouTube | `0c170ef` (P24) | `youtube {client_id: ${YOUTUBE_CLIENT_ID}, client_secret: ${YOUTUBE_CLIENT_SECRET}, refresh_token: ${YOUTUBE_REFRESH_TOKEN}, channels: [${YOUTUBE_CHANNEL_ID}], quota {daily_units: 10000, write_reserve_units: 1000}}` ; rafraîchissement, une scrutation, un `chat.write` | **Non exécuté** — aucun identifiant YouTube (`PHASE3_TRIAL_YOUTUBE` et `YOUTUBE_*` absentes) ; l'essai exige aussi un direct actif | Jeton, cadence, registre de quota et envoi éprouvés contre le point de jeton et l'API de chat scriptés (AC36–AC38) | 2026-09-27 |
| veille d'écran | `0c170ef` (P24) | `capture {sources.screen {kind: command, argv: [ffmpeg, -loglevel, error, -f, x11grab, -video_size, 640x480, -i, ":0", -frames:v, 1, -f, image2pipe, -vcodec, png, -]}}`, `watch {channels: [trial/screen], interval_seconds: 15, activation: startup, max_active_seconds: 60}` ; règle `screen.capture` pour le principal `brain.watch` | Exécuté : `PHASE3-TRIAL veille d'écran commit=0c170ef4dc0c outcome=success,ticks=2,captured=2,content_type=image/png,width=640,height=480 date=2026-09-27` | Session Wayland capturée par XWayland : contenu de l'image non vérifié (peut être noir) ; démarrage par `activation: startup`, pas par `!watch` sur une vraie chaîne ; pas de modèle réel (le corps du run appelle la capture directement) ; 2 ticks seulement, plafond horaire non atteint | 2026-09-27 |

## Lecture des manifests de runs

[runs/](runs/) conserve les cinq manifests originaux, sans suppression ni
modification. Ce sont des traces du pipeline (identifiant, dates, modèles,
verdicts, findings et coûts estimés lorsqu'ils sont disponibles), distinctes des
manifests de modules `modules/*/module.yaml`.

| Étape | Manifest du run | Résultat enregistré |
| --- | --- | --- |
| P6 | [296cfce8-a192-4975-847c-b31e64116343](runs/296cfce8-a192-4975-847c-b31e64116343/manifest.md) | `REJECT` sur trois rounds. |
| P6 | [86becf2b-7a02-4c6a-a132-b70e5f2f1748](runs/86becf2b-7a02-4c6a-a132-b70e5f2f1748/manifest.md) | Run interrompu : `run_aborted: true`, `partial: true`, phase `interrupted`. |
| P6 | [9697525f-37ba-46d1-96fa-21f33074bb3e](runs/9697525f-37ba-46d1-96fa-21f33074bb3e/manifest.md) | `INFRA_FAILURE` au round 0. |
| P7 | [3db8dfda-9658-4ca1-9f1e-7fb7cf4832b0](runs/3db8dfda-9658-4ca1-9f1e-7fb7cf4832b0/manifest.md) | `INFRA_FAILURE` au round 0. |
| P8 | [8fd87afb-b05f-4ea6-b303-e3e57edfa624](runs/8fd87afb-b05f-4ea6-b303-e3e57edfa624/manifest.md) | `APPROVED` global ; `APPROVE` au round 1, avec la limite décrite plus haut. |

- `REJECT` indique le rejet de ce run par le pipeline ; il ne décrit pas
  automatiquement l'état des corrections livrées ensuite.
- `INFRA_FAILURE` indique un échec d'infrastructure du run. Zéro finding ne
  signifie pas que le code est conforme ; le manifest seul ne précise pas la
  cause technique de cet échec.
- Un manifest partiel/interrompu ne fournit pas de verdict final de validation.
- `APPROVED` rapporte une approbation historique. Ces manifests ne contiennent
  pas de SHA de l'arbre vérifié ni de sortie pytest complète permettant de
  certifier le commit finalement livré. Les correspondances étape/commit de
  cet index proviennent de l'historique Git, pas d'une attestation des manifests.
