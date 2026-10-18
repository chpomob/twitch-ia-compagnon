# Full adversarial review — twitch-ia-compagnon

Arbre : `main` @ `29384cc`, worktree détaché. Deux relecteurs indépendants avec des lentilles différentes,
cross-review mutuelle, puis vérification à la main par l'orchestrateur de tout finding majeur ou plus.

## Méthode

- **Codex — lentille architecte** (architecture, sécurité, concurrence, bornage, extensibilité) : 13 findings ;
  16 fichiers `core/`, 37 entrées de modules, 13 docs, 36 fichiers de tests lus.
- **Claude — lentille inspecteur** (bugs concrets, robustesse, honnêteté des tests, dérive docs) : 25 findings ;
  14 fichiers `core/`, 15 modules, 11 docs, 16 fichiers de tests lus.
- **Cross-review** : Codex sur Claude — 24 VALIDATE, 1 CHALLENGE ; Claude sur Codex — 12 VALIDATE, 1 CHALLENGE.
  Les sévérités ci-dessous sont celles d'après cross-review.
- **Vérification** : `REPRODUIT` / `CONFIRMÉ` = contrôlé dans l'arbre, la plupart avec un contre-exemple
  exécuté dans le worktree ; `CODE` = lu et cohérent, sans sonde.

## Convergence (trouvé indépendamment par les deux modèles)

- **idempotence concurrente du `call_id`** — Claude F1 / Codex F7, chacun l'a reproduite.
- **commande de l'UI dans le README** — Claude F6 / Codex F13, vérifiée en l'exécutant.
- **connexions proxy sans admission ni délai hello** — Claude F9 / Codex F8.

## 1. Frontières de sécurité et de confiance

| # | Sév. | Emplacement | Défaut | Cross | Vérif. |
|---|------|-------------|--------|-------|--------|
| 1 | MAJOR | `modules/twitch/__init__.py:1521` | The supported channel.chat.send compatibility route bypasses default-deny authorization. | VALIDATE | CONFIRMÉ |
| 2 | MAJOR | `modules/agent_link/__init__.py:1083` | The agent executes calls for actions outside its configured and negotiated action set. | VALIDATE | CODE |
| 3 | MINOR | `core/actions.py:1197` | A retained call ID returns another principal's cached observation before authorization or identity validation. | VALIDATE | CONFIRMÉ |
| 4 | MINOR | `core/loader.py:1185` | Configuration error paths expose configured credentials when those values occur in mapping keys. | VALIDATE | REPRODUIT |
| 5 | MINOR | `modules/agent_link/__init__.py:990` | An arbitrary peer error code can disclose the pairing token through the agent's diagnostic reporter. | VALIDATE | CODE |
| 6 | MAJOR | `modules/brain/__init__.py:1855` | !forgetme erases only the viewer_memory file; the brain's in-process ConversationMemory for that same (platform, channel, viewer) key is never forgotten and keeps feeding the viewer's past text into prompts. | VALIDATE | CONFIRMÉ |

## 2. Défaillances silencieuses et intégrité

| # | Sév. | Emplacement | Défaut | Cross | Vérif. |
|---|------|-------------|--------|-------|--------|
| 7 | MAJOR | `core/actions.py:1197` | Replay protection covers only finished calls: two concurrent invoke() calls with the same call_id both reach the provider, so one chat.write can be sent twice. | VALIDATE | REPRODUIT |
| 8 | MAJOR | `modules/streamerbot/__init__.py:2399` | A failing supervision.emit/ready/degraded inside _attempt (reconcile trace at 2232) kills the owner task: the socket stays open, the bridge is never marked ready, and it never reconnects. | VALIDATE | CONFIRMÉ |
| 9 | MAJOR | `modules/twitch/__init__.py:2034` | A refused token (401/403 on the chat subscription) is treated as transient: EventSub recovery retries forever at the 5 s cap, opening a socket and POSTing a subscription each time, and never reports module.degraded. | VALIDATE | CONFIRMÉ |
| 10 | MAJOR | `modules/twitch/__init__.py:2044` | No EventSub keepalive watchdog: a half-open socket blocks receive() forever and chat input stops silently. | VALIDATE | CONFIRMÉ |
| 11 | MAJOR | `modules/youtube/__init__.py:1388` | The first list of a freshly resolved live chat (page_token None at start and after every 403/404 re-lookup) ingests and admits the whole existing backlog as if new, so after a restart the companion answers old messages. | VALIDATE | CODE |
| 12 | MINOR | `modules/brain/__init__.py:4547` | Model responses have no byte bound before JSON decoding, and reported usage can admit arbitrarily large replies. | VALIDATE | — |
| 13 | MAJOR | `modules/proxy/__init__.py:982` | Unauthenticated proxy connections have neither an admission cap nor a hello deadline. | VALIDATE | — |
| 14 | MINOR | `modules/agent_link/__init__.py:1105` | The bounded agent call cache does not bound running executions or duplicate-call waiter tasks. | VALIDATE | — |

## 3. Première installation et documentation

| # | Sév. | Emplacement | Défaut | Cross | Vérif. |
|---|------|-------------|--------|-------|--------|
| 15 | MAJOR | `docs/installation.md:112` | The installation guide supplies model base URLs although the adapter requires the complete Chat Completions URL. | VALIDATE | CONFIRMÉ |
| 16 | MINOR | `README.md:130` | The README quick start launches the config UI with a command that cannot run. | VALIDATE | CONFIRMÉ |
| 17 | MINOR | `docs/installation.md:353` | The install guide names the overlay file config.local.yaml while its profile is mon-compagnon.yaml; the code writes mon-compagnon.local.yaml. | VALIDATE | — |
| 18 | NIT | `tests/test_installation_doc.py:117` | test_the_guide_prints_the_commands_the_code_still_has only greps the guide text; it never checks the code exposes those commands, and README commands are not checked at all (how F6 slipped through). | VALIDATE | — |

## 4. Robustesse et mineurs

| # | Sév. | Emplacement | Défaut | Cross | Vérif. |
|---|------|-------------|--------|-------|--------|
| 19 | MINOR | `modules/proxy/__init__.py:982` | A peer that connects and never sends hello is held forever with no deadline and no cap on unpaired connections. | VALIDATE | — |
| 20 | MINOR | `modules/brain/__init__.py:4660` | Deeply nested tool-call arguments raise an uncaught RecursionError, failing the run (run_failed) instead of yielding the malformed_arguments observation; in the probe (4352) prepare raises RecursionError instead of BrainModuleError. | VALIDATE | — |
| 21 | MINOR | `modules/viewer_memory/__init__.py:930` | _write evicts files before _atomic_write; an OSError (disk full, EIO) from the write is not a MemoryStoreError, so the evictions already done are never published as memory.removed and files were deleted for a write that never happened. | VALIDATE | — |
| 22 | MINOR | `modules/viewer_memory/__init__.py:1001` | A crash between mkstemp and os.replace leaves a .viewer-memory-*.tmp file that no scan ever removes or counts toward max_total_bytes. | VALIDATE | — |
| 23 | MINOR | `core/admission.py:873` | Sessions dropped by expiry/eviction are removed from _queues but left in the _ready deque, which grows past max_sessions while workers are busy and makes every admit an O(n) membership scan. | VALIDATE | — |
| 24 | MINOR | `modules/twitch/__init__.py:213` | Twitch chat.write has no length bound (YouTube refuses >200, Kick >500), so a long model answer is sent, refused by Helix with 400 and lost as platform_rejected. | VALIDATE | — |
| 25 | MINOR | `modules/kick/__init__.py:228` | The dedup window lower bound equals the timestamp tolerance (300 s), but a signed delivery is accepted over a 600 s span (/now - sent_at/ <= 300), so with window_seconds=300 a replay can outlive its dedup entry. | VALIDATE | — |
| 26 | MINOR | `core/contracts.py:873` | Misspelled profile keys are silently accepted by --check-config for modules whose settings_schema lacks additionalProperties:false (e.g. twitch) and at top level, so a typo'd setting simply does nothing. | VALIDATE | — |
| 27 | MINOR | `modules/brain/__init__.py:3664` | Nothing asserts the history token budget or chronological order: mutations that remove the budget or the 50% output reservation leave every brain test green. | VALIDATE | — |
| 28 | NIT | `tests/test_automation_bridge.py:2221` | test_ac16_no_run_observation_is_a_success passes vacuously when run alone, under -k, xdist or random order, because it reads a module-level list filled only by other tests. | VALIDATE | — |
| 29 | NIT | `core/actions.py:1086` | cancel_grace_seconds accepts NaN/inf; asyncio.wait(timeout=nan) never returns, so a provider swallowing CancelledError would block invoke forever. | VALIDATE | — |
| 30 | NIT | `modules/audio_output/__init__.py:693` | stop always sleeps the full grace period after SIGTERM even if the player has already exited, delaying shutdown and the next playback. | VALIDATE | — |
| 31 | NIT | `modules/viewer_memory/__init__.py:161` | The scan regex uses `$`, which matches before a trailing newline, so a foreign file named '<64 hex>.json\n' is read, judged corrupt and deleted despite the docstring promising anything else is neither read, counted nor deleted. | VALIDATE | — |
| 32 | NIT | `tests/test_bus.py:384` | Tautological assertion: a constructor never returns None. | VALIDATE | — |
| 33 | NIT | `core/bus.py:214` | _retain deep-copies the event after every handler has run and outside the PublicationError wrapper, so an uncopyable payload makes publish raise a raw TypeError after all side effects happened, with no history record. | VALIDATE | — |
| 34 | MINOR | `core/actions.py:1246` | A stalled trace subscriber can defeat an action's deadline and leave cancellation without a terminal observation. | CHALLENGE | CROISÉ |
| 35 | MINOR | `core/config_ui/__init__.py:5304` | Check's event-loop cleanup waits forever for validators that the loader already abandoned at its deadline. | VALIDATE | — |

## Détail des défauts vérifiés

### 1. The supported channel.chat.send compatibility route bypasses default-deny authorization.

`modules/twitch/__init__.py:1521` — codex F3. `prepare` abonne `handle_chat_send` (`twitch:1115`) qui appelle `_send(route=compat)` directement ; `docs/design-v2.md:584` interdit explicitement de contourner l'exécuteur.

Correctif suggéré : Translate compatibility sends into ActionCalls with an explicit principal and destination, then invoke the shared executor. Require a configured grant before any compatibility send reaches the transport.

### 3. A retained call ID returns another principal's cached observation before authorization or identity validation.

`core/actions.py:1197` — codex F2. `_outcomes[call_id]` est retourné avant la résolution du spec et la politique (`actions:1197-1201`) ; la clé de cache est `call_id` seul, jamais `(principal, action)`.

Correctif suggéré : Retain an immutable call fingerprint alongside each outcome and reject reuse with different principal, session, action, destination or arguments. Preserve cached results only for an authenticated replay of the same logical call.

### 4. Configuration error paths expose configured credentials when those values occur in mapping keys.

`core/loader.py:1185` — codex F1. `module 'streamerbot': field 'settings.review-secret-4521': is not an allowed property` — le secret est dans le **champ**, seul le motif est redacté.

Correctif suggéré : Redact known credential values from diagnostic paths as well as reasons. Preserve unresolved key labels during environment resolution, and sanitize schema-error fields using the configured credential set before reporting them.

### 6. !forgetme erases only the viewer_memory file; the brain's in-process ConversationMemory for that same (platform, channel, viewer) key is never forgotten and keeps feeding the viewer's past text into prompts.

`modules/brain/__init__.py:1855` — claude F5. aucun appel à `ConversationMemory.forget()` dans tout le dépôt ; `!forgetme` n'efface que le fichier `viewer_memory`.

Correctif suggéré : On erasure publish a fact/service call the brain handles with memory.forget(SessionKey(...)); stamp an erasure epoch per key in viewer_memory and refuse memory.record from runs admitted before it. Test: forget then assert brain recall() is empty and a late record is refused.

### 7. Replay protection covers only finished calls: two concurrent invoke() calls with the same call_id both reach the provider, so one chat.write can be sent twice.

`core/actions.py:1197` — claude F1. deux `invoke()` concurrents avec le même `call_id` atteignent le provider **2 fois** (`provider invocations: 2 | handler ran: 2`) ; le test existant ne couvre que le rejeu séquentiel après terminaison.

Correctif suggéré : Keep an in-flight map call_id -> Future[ActionObservation], set it before the first await in invoke(); a second caller awaits it (asyncio.shield); pop it in finally after _record. Add a concurrent-duplicate test.

### 8. A failing supervision.emit/ready/degraded inside _attempt (reconcile trace at 2232) kills the owner task: the socket stays open, the bridge is never marked ready, and it never reconnects.

`modules/streamerbot/__init__.py:2399` — claude F2. `Supervision.emit` propage par contrat (`core/runtime.py:436-445`) ; la boucle propriétaire du pont ne rattrape que `CancelledError` (`streamerbot:2343`) : la connexion reste ouverte et le superviseur meurt.

Correctif suggéré : Guard the reconcile emit and the ready/degraded calls in _attempt/_failed/_lose with try/except Exception (as _trace_decision does), or catch Exception per iteration in _own: close and clear self._connection, report degraded, back off and continue. Add a test with a raising emit.

### 9. A refused token (401/403 on the chat subscription) is treated as transient: EventSub recovery retries forever at the 5 s cap, opening a socket and POSTing a subscription each time, and never reports module.degraded.

`modules/twitch/__init__.py:2034` — claude F3. la récupération EventSub réessaie `min(0.25*2**n, 5.0)` sans fin en avalant `TwitchModuleError`, sans `degraded` (`twitch:1968-1983`).

Correctif suggéré : Treat a 401/403 refusal of the chat subscription as terminal: report module.degraded (auth reason) and stop the loop (or at minimum raise the cap to >=60 s with jitter).

### 10. No EventSub keepalive watchdog: a half-open socket blocks receive() forever and chat input stops silently.

`modules/twitch/__init__.py:2044` — claude F4. `ws_connect` sans `heartbeat` ni timeout (`twitch:1794`), `receive()` sans échéance (`twitch:2044`) : socket semi-ouvert = chat muet.

Correctif suggéré : Store keepalive_timeout_seconds from the welcome; wrap receive in asyncio.wait_for(…, keepalive + margin) (or ws_connect(receive_timeout=…)); on timeout diagnose and return _ConsumeResult('retry'). Add a test with a silent fake socket.

### 15. The installation guide supplies model base URLs although the adapter requires the complete Chat Completions URL.

`docs/installation.md:112` — codex F12. le guide donne `https://api.openai.com/v1` alors que l'adaptateur POSTe l'endpoint tel quel (les tests utilisent `…/v1/chat/completions`).

Correctif suggéré : Document complete request URLs ending in /chat/completions, consistently in both installation guides, or explicitly implement and document base-URL expansion. Add a fake-backed tutorial startup check that asserts the actual request URL.

### 16. The README quick start launches the config UI with a command that cannot run.

`README.md:130` — claude F6. `python -m twitch-ia-compagnon-config-ui` → `No module named` ; c'est un `console_script` (`pyproject.toml:31`).

Correctif suggéré : Replace with `python -m core.config_ui --config my-config.yaml` (or the bare console script), and extend the command-existence test to README.

## Ajusté en cross-review

- `claude:F8` **CHALLENGÉ** — le retour au `min_delay` après une perte est documenté (`streamerbot:2328`) :
  une instance qui flappe est bien reconnectée au minimum, c'est le comportement spécifié. Écarté.
- `codex:F6` **CHALLENGÉ** — le mécanisme est réel mais le seul abonné `action.*` est l'audit,
  synchrone et non bloquant ; à garder comme note de robustesse.
- Trois findings notés BLOCKER par Codex (F1, F2, F4) ont été ramenés à `minor` par Claude parce qu'ils
  exigent une entrée forgée ; le jugement retenu est `major` quand l'entrée est contrôlée par le modèle
  (le `call_id`, le `principal`), `minor` quand il faut un module hostile.
- Aucun finding n'est resté sans verdict.

## Ce que cette revue ne peut pas dire

- Aucune plateforme réelle, instance Streamer.bot, OBS, service de modèle ou périphérique audio contacté :
  toute affirmation de compatibilité demande l'instance réelle.
- La suite de tests a été échantillonnée, pas lue ligne à ligne (36 et 16 fichiers) : les invariants que
  personne n'asserte sont l'endroit le plus probable des défauts restants.
- Pas de test de charge, pas d'essai de durabilité après kill, pas de smoke test du wheel installé.
- `clips`, `watch`, `moderation`, `audio_input` : non relus en profondeur par aucun des deux modèles.

## Lecture du résultat

La discipline du noyau que le gate de phase 5 a approuvée tient : files bornées, propriété unique des
entrées, issues honnêtes, diagnostics sans valeur. Ce que cette revue ajoute, c'est que les défauts qui
comptent vivent **aux frontières entre modules** — une route de compatibilité qui saute l'exécuteur, un
agent qui fait confiance au `principal` choisi par son pair, un diagnostic de réglages qui recopie une
clé, un effacement qui s'arrête au module propriétaire du fichier. Ce sont des défauts d'intégration :
précisément ce qu'une revue par commit ne peut pas voir, et ce qui justifie une passe adversariale sur
l'arbre entier.

