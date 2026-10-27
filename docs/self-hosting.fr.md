# Héberger le compagnon soi-même avec Docker

[English](self-hosting.md) · **Français**

Ce guide s'adresse à qui veut faire tourner le compagnon sur un serveur — un VPS, un NAS, une machine
Linux de récupération — sans avoir lu ce dépôt. Il demande Docker Engine avec le greffon Compose v2 sous
Linux, et rien d'autre du projet : pas de copie du dépôt, pas de Python, pas de construction d'image à la
main. Le texte anglais fait foi ; cette traduction le suit.

Là où ce guide et la spécification de la phase 10 (`docs/campaigns/phase10/spec.md`) diffèrent, la
spécification l'emporte. Une fois le compagnon lancé, les écrans de configuration et la console sont
ceux que décrit [`docs/first-run.fr.md`](first-run.fr.md) ; ce guide couvre ce qui change dans un
conteneur.

## 1. Démarrage rapide

Créez un dossier vide sur le serveur, ouvrez un terminal dedans, et tapez ces trois commandes :

```sh
curl -fsSLO https://raw.githubusercontent.com/chpomob/twitch-ia-compagnon/main/compose.yaml
docker compose up -d
docker compose logs companion
```

1. La première commande télécharge le fichier compose, et lui seul, dans le dossier.
2. `docker compose up -d` démarre le compagnon. **Le premier `up` construit l'image** : aucune image
   n'est publiée dans un registre, donc Compose la construit depuis l'URL Git du dépôt que nomme le
   fichier compose. Cette construction demande `git` installé sur l'hôte (Compose clone le dépôt pour
   elle), et elle n'a lieu qu'une fois : les `up` suivants réutilisent l'image construite. Une phase ultérieure pourra remplacer cette construction par une
   image publiée.
3. `docker compose logs companion` affiche le journal du conteneur. Deux lignes donnent l'accès,
   chacune étiquetée et chacune copiée seule : `Configuration UI: http://127.0.0.1:8765/` est l'adresse
   de la page et `Access token: …` est le jeton. Si le journal est encore vide, le compagnon démarre
   encore : relancez la commande. Chaque démarrage imprime une nouvelle paire ; la dernière est la bonne
   (voir [Le jeton](#6-le-jeton)).

L'adresse d'accès est l'adresse suivie de `?token=` et du jeton :
`http://127.0.0.1:8765/?token=…`. Ouvrez-la dans un navigateur sur le serveur lui-même, ou — sur un
serveur distant — à travers un tunnel SSH depuis votre propre ordinateur (voir [Le jeton](#6-le-jeton)).
Sur un volume vide, elle ouvre les écrans de configuration ; passez-les comme le décrit
[`docs/first-run.fr.md`](first-run.fr.md), de l'écran 1 jusqu'à l'essai à blanc de l'écran 5.

**Si le dépôt est privé**, la première commande et la construction demandent un accès en lecture au
dépôt : l'URL brute répond alors 404 à un `curl` anonyme, et la construction Git échoue. Procurez-vous
le fichier compose autrement (copié par quelqu'un qui a l'accès) et donnez à Docker des identifiants pour
le dépôt, ou construisez depuis une copie du dépôt comme le décrit
[Le fichier compose ligne à ligne](#2-le-fichier-compose-ligne-à-ligne).

## 2. Le fichier compose ligne à ligne

Le fichier compose déclare un service, `companion`, et un volume nommé. Rien n'y est propre à Docker
Desktop, et aucun autre fichier compose n'est nécessaire.

| Ligne | Ce qu'elle fait |
|---|---|
| `build: context: "${TWITCH_IA_COMPAGNON_BUILD_CONTEXT:-https://github.com/chpomob/twitch-ia-compagnon.git#main}"` | d'où l'image est construite : la branche `main` du dépôt par défaut. Donnez à `TWITCH_IA_COMPAGNON_BUILD_CONTEXT` un autre contexte — par exemple `.` depuis une copie du dépôt — pour construire à partir de lui |
| `image: twitch-ia-compagnon:local` | le nom local de l'image construite ; elle n'est envoyée nulle part |
| `init: true` | un processus init minimal comme processus 1, pour que les signaux atteignent le compagnon et que les enfants terminés soient récoltés |
| `restart: unless-stopped` | Docker redémarre le conteneur après un plantage ou un redémarrage de la machine, sauf si vous l'avez arrêté vous-même |
| `ports: - "127.0.0.1:8765:8765"` | la page est publiée **sur la boucle locale de l'hôte seulement** : rien hors du serveur ne l'atteint. Voir [Le jeton](#6-le-jeton) pour l'exposer plus loin |
| `volumes: - "companion-data:/data"` | tous les fichiers que garde le compagnon sont dans ce seul volume, monté sur `/data` (voir [Le volume `/data`](#3-le-volume-data)) |
| `healthcheck:` | lance `twitch-ia-compagnon-config-ui --health` toutes les 30 s (délai 10 s, 3 essais, période de démarrage 90 s), comme le déclare l'image (voir [Les états du healthcheck](#9-les-états-du-healthcheck)) |
| `# env_file:` / `# environment:` | en commentaire : comment donner des identifiants au conteneur sous forme de variables d'environnement (voir [Variables d'environnement et fichier d'identifiants](#5-variables-denvironnement-et-fichier-didentifiants)) |
| `# secrets:` / `# command:` | en commentaire : un jeton d'accès stable lu dans un fichier (voir [Le jeton](#6-le-jeton)) |
| `volumes: companion-data: {}` | déclare le volume nommé ; Compose le préfixe du nom du projet, qui est le nom du dossier |

L'image elle-même démarre l'interface de configuration en mode conteneur. Sa commande par défaut est :

```sh
twitch-ia-compagnon-config-ui --host 0.0.0.0 --allow-non-loopback --no-browser --start-runtime
```

- `--host 0.0.0.0` écoute sur toutes les interfaces **à l'intérieur du conteneur**, pour que le port
  publié atteigne la page. Avec `--allow-non-loopback`, qu'exige une telle écoute, le compagnon écrit
  un avertissement dans son journal à chaque démarrage (`config_ui: warning: bound to 0.0.0.0 port 8765: …`) :
  la page est joignable depuis le réseau et protégée seulement par le jeton d'accès. Avec ce fichier
  compose, « le réseau » est la boucle locale de l'hôte, par le port publié.
- `--no-browser` n'essaie jamais d'ouvrir un navigateur : il n'y en a pas dans un conteneur. L'adresse
  et le jeton sont toujours imprimés une fois, sur deux lignes étiquetées distinctes
  (`Configuration UI:` et `Access token:`), pour pouvoir copier l'un sans l'autre.
- `--start-runtime` démarre le compagnon dès que la page écoute, par le même **Start** que la console,
  quand la configuration est terminée. Avec une configuration inachevée, ou sans profil, il ne démarre
  rien et journalise pourquoi, sans aucune valeur.
- `--health` est le healthcheck : il imprime une ligne et se termine.

Deux autres options existent et sont inactives par défaut : `--token-file` (voir [Le jeton](#6-le-jeton))
et `--allowed-host` (un nom d'hôte accepté pour une écoute sur toutes les interfaces). `--port` change le
port d'écoute, et `--config` désigne un autre profil que celui par défaut ; le fichier compose n'utilise
ni l'un ni l'autre.

## 3. Le volume `/data`

L'image fixe `XDG_CONFIG_HOME=/data` et `HOME=/data`, donc le dossier par utilisateur du compagnon est
`/data/twitch-ia-compagnon/`. Le profil, le fichier d'identifiants et tous les enregistrements y sont,
côte à côte. Rien d'autre de ce qu'écrit le compagnon n'atterrit ailleurs dans le conteneur, hormis des
fichiers temporaires.

| Fichier | Rôle | Écrit par |
|---|---|---|
| `/data/twitch-ia-compagnon/profile.yaml` | le profil : les modules, leurs réglages et des références `${NAME}` à vos identifiants, jamais leurs valeurs | l'écran 4 de la configuration |
| `/data/twitch-ia-compagnon/profile.local.yaml` | la surcouche : vos modifications faites dans l'éditeur, posées sur le profil ; absente tant que vous n'utilisez pas l'éditeur | l'éditeur |
| `/data/twitch-ia-compagnon/profile.yaml.status.json` | l'enregistrement d'état : le processus du compagnon en marche et l'état de chaque module, que lisent la console et le healthcheck | le compagnon pendant qu'il tourne |
| `/data/twitch-ia-compagnon/profile.yaml.feed.jsonl` | le fil : ce qui vient de se passer, tel que la console le montre | le compagnon pendant qu'il tourne |
| `/data/twitch-ia-compagnon/profile.yaml.wizard.json` | l'avancement de la configuration : quels écrans sont passés | la configuration |
| `/data/twitch-ia-compagnon/profile.yaml.credentials.json` | le fichier d'identifiants : les valeurs de vos clés et jetons (mode `0600`) | l'écran 4 de la configuration |
| `/data/twitch-ia-compagnon/profile.yaml.instance.json` | l'enregistrement d'instance : le processus de la page, son port, son marqueur et son adresse d'accès (donc son jeton) ; présent tant que la page tourne | la page, au démarrage ; supprimé à un arrêt propre |
| `/data/twitch-ia-compagnon/profile.yaml.supervisor.json` | l'enregistrement de supervision : si cette page a démarré le compagnon (`not_started`, `launched` avec son processus, ou `stopped`), que lit le healthcheck | la page ; supprimé à un arrêt propre |

Le fichier d'identifiants et l'enregistrement d'instance contiennent des secrets : traitez tout le
volume comme un mot de passe.

**Propriétaire.** `/data` appartient à l'utilisateur numérique `10001` (groupe `10001`) avec le mode
`0700`, et le conteneur tourne sous cet utilisateur, jamais en root. Un volume nommé, comme celui du
fichier compose, hérite de lui-même de ce propriétaire. Si vous le remplacez par un **montage d'un
dossier de l'hôte** (par exemple `./data:/data`), ce dossier doit appartenir à l'uid `10001` et ne doit
être accessible en écriture ni à son groupe ni aux autres :

```sh
sudo chown 10001:10001 data && sudo chmod 700 data
```

Sinon le compagnon ne peut pas y écrire, ou — pour un dossier qui appartient à un autre utilisateur ou
que d'autres peuvent modifier — il refuse d'enregistrer vos identifiants où que ce soit, le dit, et
l'écran 4 de la configuration reste fermé, exactement comme sur un PC.

## 4. Premier lancement et restauration

- **Un volume vide est un premier lancement.** L'adresse d'accès ouvre les écrans de configuration. Une
  fois l'écran 5 passé, la page montre la console ; avec `--start-runtime` le compagnon est démarré au
  prochain démarrage du conteneur — ou tout de suite avec le **Start** de la console.
- **Un volume restauré reprend où il en était.** Un conteneur démarré sur un volume qu'un conteneur
  précédent a utilisé — arrêté, tué ou planté — arrive là où celui-ci serait arrivé : la console pour
  une configuration terminée, la configuration pour une configuration inachevée. Il garde les
  identifiants enregistrés et, avec `--start-runtime`, redémarre le compagnon.
- **Les enregistrements laissés derrière ne bloquent rien.** Un conteneur tué laisse ses
  enregistrements d'instance et d'état, qui nomment des numéros de processus qu'un processus du nouveau
  conteneur peut reprendre. Un processus enregistré qui a démarré après l'écriture de son
  enregistrement n'est pas celui qui l'a écrit, donc l'enregistrement est tenu pour périmé : la nouvelle
  page sert, et **Start** n'est pas refusé à cause de lui.

## 5. Variables d'environnement et fichier d'identifiants

Vos identifiants arrivent au compagnon par l'une de deux voies, jamais par l'image :

- **par les écrans de configuration**, qui les enregistrent dans le fichier d'identifiants du volume,
  `profile.yaml.credentials.json` ;
- **comme variables d'environnement données au conteneur** : décommentez `env_file:` (un fichier comme
  `companion.env` à côté du fichier compose, un `NAME=value` par ligne, tenu hors de tout dépôt) ou
  `environment:` dans le fichier compose. Le compagnon en hérite.

Le profil ne contient jamais que des références comme `${TWITCH_ACCESS_TOKEN}` ; une valeur n'y entre que
par une telle référence.

**Priorité : un identifiant enregistré l'emporte sur une variable héritée du même nom.** Quand la
console démarre le compagnon, une valeur enregistrée dans le fichier d'identifiants l'emporte sur une
variable d'environnement du même nom donnée au conteneur. C'est la priorité de toute installation,
inchangée. Changer une telle variable dans `env_file:` ne change donc pas un identifiant que la
configuration a déjà enregistré.

Aucune valeur d'identifiant n'est jamais écrite dans le journal du conteneur ni dans le journal de santé.

## 6. Le jeton

- **Imprimé une fois par démarrage.** Sans `--token-file`, le jeton d'accès est généré à chaque
  démarrage du conteneur, et imprimé une fois dans le journal sur sa propre ligne `Access token:`. Un
  redémarrage en crée un nouveau : relisez-le avec `docker compose logs companion` — la dernière ligne
  `Access token:` est la bonne. Quiconque peut lire le journal du conteneur peut lire le jeton.
- **Un jeton stable : `--token-file`.** Avec `--token-file PATH` le jeton est le contenu de ce fichier,
  identique d'un redémarrage à l'autre. Il doit faire de 43 à 128 caractères pris dans
  `A–Z a–z 0–9 - _` ; les blancs autour sont ignorés. Un fichier absent, vide ou invalide arrête la page
  au démarrage (code de sortie 2), avec un message qui nomme le fichier et la règle enfreinte, jamais
  son contenu. Pour l'utiliser, créez le fichier à côté du fichier compose, par exemple :

  ```sh
  python3 -c "import secrets; print(secrets.token_urlsafe(32))" > companion_token.txt
  ```

  Le conteneur lit ce fichier sous l'uid `10001` : donnez-le à cet utilisateur et cachez-le aux autres
  avec `sudo chown 10001 companion_token.txt && sudo chmod 600 companion_token.txt`. Décommentez ensuite
  les entrées `secrets:` et `command:` du fichier compose et son bloc `secrets:` de premier niveau : le
  fichier est monté en `/run/secrets/companion_token`, et la commande reprend celle de l'image en
  ajoutant `--token-file /run/secrets/companion_token`. Le jeton n'est jamais stocké dans l'image.
- **Depuis votre propre ordinateur : un tunnel SSH.** La page n'est publiée que sur la boucle locale du
  serveur. Depuis votre ordinateur, `ssh -L 8765:127.0.0.1:8765 you@server` la fait suivre, et
  l'adresse d'accès fonctionne alors, écrite comme ci-dessus, dans votre propre navigateur. Rien ne change dans
  le fichier compose.
- **L'exposer au-delà de l'hôte** est une modification explicite, jamais un défaut. Changez l'adresse
  publiée (par exemple `"8765:8765"` pour toutes les interfaces du serveur) et ajoutez à la commande un
  `--allowed-host NAME` par nom d'hôte que votre navigateur utilisera pour l'atteindre (un nom ou une
  adresse), puis remplacez `127.0.0.1` dans l'adresse imprimée par ce nom. Gardez le port `8765` des
  deux côtés : la page vérifie le nom et le port que porte chaque requête. La page parle HTTP en clair,
  donc le jeton et la session traversent le réseau sans chiffrement : ne le faites que sur un réseau de
  confiance, en sachant que le jeton est alors la seule protection. Aucune option ne désactive le jeton.

## 7. Les quatre profils

L'image contient, inchangés, les quatre profils prêts à l'emploi de l'écran 1 de la configuration :
`chat_qa` (répond à un message du chat qui nomme le compagnon), `channel_points` (réagit à une
récompense personnalisée), `spoken_keyword` (répond à un mot-clé prononcé) et `silent` (connecté, ne
répond jamais). L'essai de la phase 10 génère chacun avec des valeurs de test dans un conteneur de
l'image, le vérifie avec `twitch-ia-compagnon --check-config` et lance son essai à blanc, comme le
font les écrans 4 et 5 de la configuration.

`spoken_keyword` passe sa vérification et son essai à blanc dans un conteneur, mais **un conteneur n'a
pas de microphone** : il ne peut pas vous entendre. Le profil est livré inchangé ; dans un conteneur,
préférez l'un des trois autres.

## 8. Sauvegarde, restauration et mise à jour

Tout ce qu'il faut garder est le volume. Arrêtez d'abord le compagnon, pour qu'aucun fichier ne change
pendant la copie. Les commandes ci-dessous utilisent l'image elle-même, lancée sous l'uid `10001`, pour
que les fichiers restaurés gardent leur propriétaire :

```sh
docker compose stop
docker compose run --rm --no-deps -T --entrypoint tar companion -C /data -czf - . > companion-data.tgz
docker compose start
```

Pour restaurer cette archive — sur le même serveur ou un autre, dans un dossier qui contient le même
fichier compose — arrêtez le compagnon, videz le volume et décompressez-y l'archive (Compose crée le
volume au besoin), puis démarrez. Le volume est vidé d'abord parce que la décompression n'écrase que les
fichiers que l'archive contient : un fichier créé après la sauvegarde, comme un `profile.local.yaml`
plus récent, resterait sinon en place et serait fusionné par-dessus le profil restauré. Sur un nouveau
serveur, l'arrêt ne fait rien et le volume est déjà vide :

```sh
docker compose stop
docker compose run --rm --no-deps -T --entrypoint sh companion -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < companion-data.tgz
docker compose up -d
```

L'archive contient vos identifiants : rangez-la comme un mot de passe. Le conteneur restauré se comporte
comme le décrit [Premier lancement et restauration](#4-premier-lancement-et-restauration).

Pour **mettre à jour**, reconstruisez l'image depuis le dépôt et redémarrez ; le volume est conservé :

```sh
docker compose build --pull --no-cache
docker compose up -d
```

`--no-cache` fait relire le dépôt par Compose au lieu de réutiliser la construction précédente. N'utilisez
jamais `docker compose down -v` sans le vouloir : `-v` supprime le volume, donc votre profil et vos
identifiants.

## 9. Les états du healthcheck

Le healthcheck, `twitch-ia-compagnon-config-ui --health`, lit ce que le compagnon écrit dans le volume,
demande `GET /instance` à la page avec le jeton d'accès (la seule requête qui n'ouvre pas de session), et
imprime une ligne. Docker en montre le résultat comme `healthy` (code 0) ou `unhealthy` (code 1, trois
fois de suite) dans `docker compose ps` ; la ligne elle-même est dans le journal de santé de
`docker inspect`. Elle ne contient jamais le jeton, une session ni un identifiant.

Il y a cinq états : `ready`, `degraded`, `starting`, `idle` et `unhealthy`. La première ligne qui
correspond décide :

| Ligne imprimée | Sens | Code |
|---|---|---|
| `unhealthy: no instance record` | aucune page n'a publié son enregistrement (pas démarrée, ou arrêtée) | 1 |
| `unhealthy: not answering` | la page ne répond pas en 5 secondes | 1 |
| `unhealthy: token refused` | la page refuse le jeton enregistré | 1 |
| `unhealthy: another instance` | autre chose répond sur le port | 1 |
| `unhealthy: supervision record unusable` | l'enregistrement de supervision est illisible | 1 |
| `unhealthy: supervision record not written` | la page n'a pas pu enregistrer un démarrage ou un arrêt (son répertoire ou son volume refuse l'écriture) : ni « au repos » ni « en marche » n'est fiable ; si même cela ne peut être enregistré, la page s'arrête et le conteneur se termine | 1 |
| `idle: wizard unfinished` | la configuration n'est pas terminée, donc rien n'est démarré | 0 |
| `idle: not started` | la configuration est terminée, le compagnon n'a pas été démarré depuis le démarrage de cette page | 0 |
| `idle: stopped` | vous avez arrêté le compagnon depuis la console | 0 |
| `unhealthy: runtime exited` | le compagnon que cette page a démarré ne tourne plus | 1 |
| `ready` | le compagnon tourne et chaque module est prêt | 0 |
| `degraded: <names>` | le compagnon tourne, ces modules (triés, séparés par des virgules) sont dégradés | 0 |
| `starting` | le compagnon a été démarré et ne s'est pas encore signalé, depuis au plus 60 s | 0 |
| `unhealthy: runtime not ready` | démarré il y a plus de 60 s et toujours muet | 1 |

La période de démarrage (90 s) dépasse ces 60 s, donc un démarrage normal n'est jamais signalé malade.

## 10. Taille de l'image

L'image mesure **147.7 MB** selon `docker image inspect --format '{{.Size}}'`,
mesurée lors de l'essai de la phase 10 ([compte rendu d'essai](campaigns/phase10/trial-record.md)).

## 11. Limites

Le mode conteneur est le même produit sans bureau autour. Ce que cela laisse de côté :

- **pas de session de bureau** : on atteint la page par une adresse lue dans le journal, pas par un
  clic sur un lanceur ;
- **pas d'ouverture de navigateur** : le compagnon n'ouvre jamais de navigateur (`--no-browser`) ; c'est
  vous qui ouvrez l'adresse ;
- **pas de lanceur** : `--install-launcher` écrit un lanceur de bureau, qui n'a pas de sens dans un
  conteneur ;
- **pas de microphone** : `spoken_keyword` ne peut pas vous y entendre (voir
  [Les quatre profils](#7-les-quatre-profils)) ;
- **pas de paquet natif** : il n'y a ni installateur ni application empaquetée ; l'image est construite
  depuis le dépôt sur votre hôte et n'est publiée dans aucun registre ;
- **pas de connexion Twitch** : l'écran 2 de la configuration demande un client id, un client secret et
  un jeton d'accès qu'il ne peut pas créer pour vous ; le
  [guide du premier lancement](first-run.fr.md#4-les-cinq-écrans-de-configuration) dit, en cinq
  étapes, d'où vient chacun.

Les autres limites du projet s'appliquent telles quelles : il n'a jamais tourné sur un vrai stream, et
ses connexions à Twitch, Kick et YouTube n'ont jamais parlé aux services réels (voir
[`docs/installation.fr.md`](installation.fr.md), section 13).
