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
