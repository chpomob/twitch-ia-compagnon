# Documentation du développement

Le [plan de développement](../plan.md) définit les étapes et leurs critères de
validation ; la [spec générale](../spec.md) décrit les exigences R1–R7 et AC1–AC21.
Les specs séparées disponibles pour P6–P8 sont dans [steps/](steps/), déplacées
sans modification, y compris leur statut historique `draft`. Il n'existe pas de
spec séparée P1–P5 dans ce dépôt : se reporter aux sections correspondantes du
plan et à la spec générale. Le plan prévoit aussi P4A et une revue finale P9 ;
ce dernier jalon ne doit pas être considéré comme validé par cet index.

## Étapes, livraisons et preuves

« Livraison » désigne ici le commit historique de l'étape, sans présumer sa
conformité. Les tests liés ci-dessous sont les fichiers de la branche courante ;
leur présence dans un ancien commit n'est pas une preuve d'exécution réussie.
La re-validation globale décrite plus bas porte sur l'état corrigé de `main`.

| Étape | Spec / périmètre dans le plan | Commit de livraison | Preuve disponible et limites |
| --- | --- | --- | --- |
| P1 — Bus asynchrone | [Plan, section P1](../plan.md), [spec R1](../spec.md) | `66b0059` | Message de squash « adversarial approved » ; pas de manifest P1 conservé ici. [Tests bus](../tests/test_bus.py) inclus dans la re-validation globale. |
| P2 — Chargement des modules | [Plan, section P2](../plan.md), [spec R2](../spec.md) | `63f5574` | Message de squash « adversarial approved » ; pas de manifest P2 conservé ici. [Tests loader](../tests/test_loader.py) inclus dans la re-validation globale. |
| P3 — Configuration et cycle de vie | [Plan, section P3](../plan.md), [spec R3](../spec.md) | `7309e2e` | Message de squash « adversarial approved » ; pas de manifest P3 conservé ici. [Tests main](../tests/test_main.py) inclus dans la re-validation globale ; corrections ultérieures du shutdown et de l'annulation. |
| P4 — Réception Twitch EventSub | [Plan, section P4](../plan.md), [spec R4](../spec.md) | `9996e0e` | Message de squash « adversarial approved » ; pas de manifest P4 conservé ici. [Tests Twitch](../tests/test_twitch.py) inclus dans la re-validation globale. Cette livraison ne contient pas P4A. |
| P4A — Envoi Twitch Helix | [Plan, section P4A](../plan.md), [spec R4](../spec.md) | `a0c3173` | Complément livré après P8 ; [tests Twitch](../tests/test_twitch.py), [brain](../tests/test_brain.py) et [intégration](../tests/test_integration.py) inclus dans la re-validation globale. |
| P5 — Brain / pipeline LLM | [Plan, section P5](../plan.md), [spec R5](../spec.md) | `965b250` | Aucun manifest P5 conservé ici, ni rapport d'exécution historique séparé. [Tests brain](../tests/test_brain.py) inclus dans la re-validation globale. |
| P6 — Middleware audit | [Spec P6](steps/step-P6-spec.md), [plan P6](../plan.md) | `bd6e7a3` | Trois runs conservés : REJECT, interrompu, INFRA_FAILURE ; aucun ne prouve l'approbation de cette livraison. [Tests audit](../tests/test_audit.py) inclus dans la re-validation globale. |
| P7 — Exemple de configuration et cohérence des manifests de modules | [Spec P7](steps/step-P7-spec.md), [plan P7](../plan.md) | `f3d07fc` | Run conservé : INFRA_FAILURE, sans approbation démontrée. [Tests examples](../tests/test_examples.py) inclus dans la re-validation globale. |
| P8 — Intégration de bout en bout | [Spec P8](steps/step-P8-spec.md), [plan P8](../plan.md) | `fcc63c9` | [Manifest APPROVED](runs/8fd87afb-b05f-4ea6-b303-e3e57edfa624/manifest.md) : verdict historique du pipeline uniquement. L'arbre livré était cassé ; voir ci-dessous. [Tests integration](../tests/test_integration.py) inclus dans la re-validation globale après corrections. |

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
