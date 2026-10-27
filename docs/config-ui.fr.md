# Interface de configuration — guide opérateur

[English](config-ui.md) · **Français**

L'interface de configuration est une petite page web pour lire et modifier la configuration de
`twitch-ia-compagnon`, la contrôler, et redémarrer le processus principal depuis cette page. Elle
tourne dans **son propre processus**, séparé du processus principal (`core.main`), et n'ajoute
aucune dépendance d'exécution au-delà de `aiohttp` et PyYAML. Chaque ressource qu'elle sert vient
du processus de l'interface lui-même : aucun CDN, aucune ressource externe.

Chaque page de module est générée à partir du manifeste du module (`module.yaml` :
`settings_schema` plus ses annotations `title`/`default`, et sa déclaration de déclencheurs).
Aucun module ne livre de HTML : ajouter un module ajoute sa page.

Là où ce guide et la spécification de la phase 4 (`docs/campaigns/phase4/spec.md`) diffèrent,
c'est la spécification qui fait foi.

## 1. Lancement

```sh
python -m core.config_ui --config config.yaml
```

Le paquet installe aussi le script de console équivalent :

```sh
twitch-ia-compagnon-config-ui --config config.yaml
```

Options :

| Option | Signification |
|---|---|
| `--config PATH` | Le fichier de configuration de base (requis). L'interface ne l'écrit jamais. |
| `--overlay PATH` | Le fichier de superposition géré (par défaut : dérivé de `--config`, §3). |
| `--host HOST` | Hôte d'écoute (par défaut `127.0.0.1`). |
| `--port PORT` | Port d'écoute (par défaut `8765`). |
| `--allow-non-loopback` | Autorise un `--host` qui n'est pas une adresse de bouclage (§2). |
| `--allowed-host NAME` | Avec une écoute générique (`0.0.0.0` ou `::`), accepte aussi l'en-tête `Host`/`Origin` `NAME:<port>`. Répétable. |
| `--status-file PATH` | Le chemin du fichier d'état (par défaut `<nom du fichier de base>.status.json`, §8). |

### Arguments de lancement après `--`

Tout ce qui suit `--` sur la ligne de commande de l'interface est l'**argumentaire de lancement** :
la commande qu'« Appliquer » exécute pour démarrer le processus principal. Il est exécuté
directement, **sans shell** (aucun guillemet, aucune expansion de variable, aucun glob) :

```sh
python -m core.config_ui --config config.yaml -- /opt/venv/bin/python -m core.main --config config.yaml
```

Sans `--`, l'argumentaire de lancement est l'interpréteur qui exécute l'interface, lançant
`-m core.main --config <base>`, plus le même `--overlay PATH` lorsqu'un chemin a été donné à
l'interface.

L'interface ne démarre pas le processus principal d'elle-même : c'est le premier « Appliquer » qui
le démarre.

## 2. Local uniquement

L'interface est **locale uniquement** : elle modifie les fichiers de configuration de la machine
sur laquelle elle tourne. Elle n'est jamais un canal d'administration à distance, et elle
**n'administre pas** un cerveau distant à travers le proxy — le protocole de proxy
(`docs/proxy-protocol.md`) ne transporte aucun trafic de configuration. Pour configurer le cerveau
sur une autre machine, lancez l'interface sur cette machine-là.

- Elle écoute une adresse de bouclage par défaut. Demander un hôte qui n'en est pas une
  (`0.0.0.0`, `::`, `192.168.1.10`, …) sans `--allow-non-loopback` la fait sortir avec un code non
  nul et un diagnostic, avant toute écoute.
- À chaque démarrage, elle tire un jeton d'accès aléatoire neuf (**au moins 128 bits**) et affiche
  l'URL d'accès, qui porte ce jeton, **une seule fois** sur la sortie standard :

  ```text
  Configuration UI: http://127.0.0.1:8765/?token=...
  ```

  Ouvrez cette URL dans un navigateur ; l'interface pose alors un cookie de session. Une requête
  sans jeton ni session valide reçoit 401/403 et aucun contenu de page. Le jeton n'apparaît nulle
  part ailleurs : ni dans une page, ni dans une trace. Redémarrer l'interface produit un nouveau
  jeton ; l'ancienne URL cesse de fonctionner.
- Pour une écoute générique, l'URL affichée utilise `127.0.0.1`.
- Chaque requête doit porter un en-tête `Host` nommant une autorité acceptée (l'hôte et le port
  d'écoute ; pour une écoute de bouclage, aussi `localhost`, `127.0.0.1` et `[::1]` avec ce port ;
  pour une écoute générique, celles-ci plus chaque `--allowed-host`). Une requête qui modifie
  l'état (enregistrer, supprimer une surcharge, contrôler, appliquer) doit être un POST muni du
  jeton CSRF de la page, et un en-tête `Origin`, lorsqu'il est présent, doit valoir exactement
  `http://<autorité acceptée>` — ou le littéral `null` qu'un navigateur envoie pour son propre
  formulaire sous une politique de référent stricte, accepté seulement avec le jeton CSRF de la
  session. Les pages sont servies avec `Referrer-Policy: same-origin`. Un POST dont le jeton CSRF est
  périmé (la page est restée ouverte pendant que la session changeait) ne modifie rien et reçoit
  `403` avec la même page, un jeton neuf et une phrase disant qu'elle a expiré : vérifiez-la et
  renvoyez-la. Un GET ne modifie jamais rien.

Au plus 16 requêtes sont admises en même temps (4 en cours, les autres attendant un fil
d'exécution), chacune avec un corps d'au plus 1 Mio. Au-delà, la requête reçoit
`503 Service Unavailable` avec `Retry-After: 1` avant même que son corps soit lu : rien n'a été
fait, soumettez-la à nouveau.

## 3. Le fichier de superposition

L'interface n'écrit jamais le fichier de base. Chaque modification va dans un **fichier de
superposition géré** que le moteur fusionne par-dessus la base intacte, de sorte que les
commentaires et la mise en forme de votre fichier de base restent tels que vous les avez écrits.

### Règle du chemin de superposition

Le chemin de superposition est le nom du fichier de base, privé d'un éventuel `.example` final,
son suffixe `.yaml`/`.yml` final remplacé par `.local.yaml`, dans le dossier du fichier de base :

| Fichier de base | Fichier de superposition |
|---|---|
| `config.yaml` | `config.local.yaml` |
| `presence.yaml.example` | `presence.local.yaml` |
| `x.yml` | `x.local.yaml` |
| `config.txt` | aucun |

`--overlay PATH` remplace cette règle, aussi bien sur l'interface que sur le moteur
(`python -m core.main --config config.yaml --overlay PATH`). Une base dont le nom ne finit ni par
`.yaml` ni par `.yml` n'a pas de superposition implicite : le moteur lit alors la base seule, et
l'interface refuse de démarrer sauf si `--overlay PATH` est fourni. L'interface refuse aussi de
démarrer lorsque le chemin de superposition est le fichier de base, avec le diagnostic
`overlay_path_collision`, quelle que soit l'orthographe du chemin.

Chaque chemin — `--config`, `--overlay`, `--status-file` et la variable du fichier d'état — est
utilisé sous une forme canonique unique, sur l'interface comme sur le moteur : `~` et
`$VAR`/`${VAR}` sont développés, le chemin est rendu absolu par rapport au dossier courant, et les
liens symboliques ainsi que `..` sont résolus. `~/config.yaml`, `./sub/../config.yaml` et un lien
vers la base nomment donc tous la même base. Un chemin qui ne peut pas être canonisé (variable non
définie, `~user` inconnu, boucle de liens) est refusé au démarrage avec le diagnostic
`path_not_canonical` ; il n'est jamais deviné.

Un fichier de superposition absent ne change rien ; un fichier de superposition vide ne signifie
aucune surcharge. Une superposition illisible, qui n'est pas du YAML valide ou qui n'est pas une
table est une erreur de configuration qui nomme le fichier de superposition (et ne cite jamais son
contenu).

### Ordre de fusion

La superposition est fusionnée en profondeur par-dessus la base avant la résolution des `${NAME}`
et la validation :

- une table dans la superposition se fusionne clé par clé dans la table de base, récursivement ;
- toute autre valeur de superposition — scalaire, liste ou `null` — remplace intégralement la
  valeur de base (les listes ne sont pas concaténées) ;
- les clés présentes uniquement dans la base sont conservées.

Exemple : base `{a: {b: 1, c: [1, 2]}, d: x}` et superposition `{a: {b: 2, c: [3]}, e: y}` donnent
le document fusionné `{a: {b: 2, c: [3]}, d: x, e: y}`. Une superposition `a: null` donne
`a: null` dans le document fusionné.

Les chemins relatifs de la configuration fusionnée se résolvent depuis le dossier du fichier de
base.

### La superposition ne peut pas supprimer une clé de la base

Une fusion en profondeur peut remplacer une clé définie dans la base, mais la superposition **ne
peut pas supprimer une clé de la base**. Une entrée définie dans la base (par exemple la politique
de déclenchement d'un canal) peut être remplacée depuis l'interface, jamais supprimée ; la page le
dit au lieu de proposer une suppression qui n'aurait pas lieu. Pour la supprimer, modifiez le
fichier de base vous-même.

« Supprimer la surcharge » efface un chemin de clé de la superposition seulement ; la valeur
effective redevient alors celle de la base.

### La superposition est réécrite par l'interface

La superposition est un fichier géré par l'interface. Chaque enregistrement la réécrit
intégralement (de façon atomique : un lecteur voit l'ancien fichier ou le nouveau, jamais un
fichier partiel), donc **les commentaires et la mise en forme de la superposition ne sont pas
conservés**. Mettez vos commentaires dans le fichier de base. Un enregistrement est refusé lorsque
la superposition a changé sur le disque depuis l'affichage de la page ; rechargez la page et
refaites la modification.

### Un `*.local.yaml` égaré est fusionné partout

Le moteur fusionne la superposition implicite que l'interface tourne ou non :
`python -m core.main --config PROFILE --check-config`, `load_config`, `check_config` et `run` la
lisent tous. Un `config.local.yaml` (ou n'importe quel `*.local.yaml`) posé à côté d'un profil —
par exemple un reste de session d'interface à côté d'un profil `*.example` — est donc fusionné par
`--check-config` aussi, et peut changer son verdict. Supprimez-le ou renommez-le pour contrôler le
profil seul. `.gitignore` tient les `*.local.yaml` hors du dépôt.

## 4. Ce que l'interface écrit

L'interface écrit exactement ces **5 blocs modifiables**, dans la superposition uniquement :

- `enabled_modules` — une bascule d'activation par module, sur la page de base ;
- `modules.<name>` — les réglages de chaque module, sur la page de ce module ;
- `triggers` — `triggers.<input>.channels.<channel>` (une politique de canal entière), sur la page
  du module nommé `<input>` ;
- `limits` — `limits.<group>.<field>`, sur la page des réglages du cœur ;
- `modules_directory` — sur la page des réglages du cœur.

Un réglage de module booléen est un choix à trois voies : « non défini (défaut : …) » le laisse
hors de la superposition et hérite du défaut déclaré, tandis que `true` et `false` écrivent
explicitement cette valeur — ainsi un `false` posé sur un `default: true` est enregistré comme
`false`. Une valeur explicite revient à « non défini » par « Supprimer la surcharge ». Une valeur
configurée qui n'est pas un booléen (la chaîne `'true'`, par exemple) est présentée comme son
propre choix « (pas un booléen) » et laissée intacte tant que vous ne choisissez pas `true` ou
`false`, ce qui la remplace par le booléen.

Ces **2 blocs en lecture seule** sont affichés mais jamais écrits :

- `secrets`
- `actions` (les règles d'autorisation en refus par défaut)

Pourquoi (décision 6c) : ils sont en lecture seule en v1 afin qu'un seul clic de travers ne puisse
jamais élargir l'ensemble des actions autorisées ni exposer un secret. Les pages le disent à côté
de chacun d'eux : « en lecture seule en v1 : les modifier pourrait élargir les actions autorisées /
exposer des secrets ». Modifiez-les à la main dans le fichier de base. Si vous écrivez `secrets` ou
`actions` à la main dans la superposition, le moteur les fusionne comme n'importe quelle autre clé,
l'interface les affiche en lecture seule avec l'origine « superposition », et chaque écriture de
l'interface les laisse inchangés.

Toute autre écriture est refusée et ne change rien : une autre clé de premier niveau, un chemin de
justificatif d'identité déclaré, un champ dont la valeur configurée est une référence `${NAME}`, ou
un parent d'un tel champ dont l'écriture le modifierait. Les valeurs sont vérifiées contre le
contrat du champ lui-même (schéma, schéma des paramètres de déclencheur, opérateurs de combinaison
supportés, type de limite, …) et un enregistrement invalide n'écrit rien. Chaque enregistrement ou
suppression accepté écrit une trace avec l'heure, la page, l'opération et les chemins des réglages —
jamais les valeurs.

**Contrôler** valide la configuration fusionnée plus les modifications non enregistrées de la page
en passant par le chemin de contrôle du moteur lui-même, sans rien écrire et sans toucher au
processus principal.

## 5. Politique sur les secrets

Aucune valeur de secret n'apparaît dans une page, une réponse, une trace de l'interface, un rapport
de redémarrage ou un diagnostic. Les valeurs de secret sont :

- la valeur résolue de toute variable d'environnement référencée où que ce soit dans la base ou la
  superposition (comme valeur ou comme clé de table) ;
- les valeurs des variables listées dans le bloc `secrets` ;
- toute valeur littérale à un chemin de justificatif d'identité déclaré.

Chaque valeur de ce genre est couverte quelle que soit sa longueur, un seul caractère inclus, et
partout où le même texte est configuré : dans la valeur d'un autre réglage, comme clé de canal de
déclenchement ou d'une autre entrée nommée, dans un diagnostic. Une clé configurée qui porte une
valeur de secret est nommée dans les contrôles du formulaire par un nom opaque `@field-…` que
l'interface sait reconvertir, jamais par son texte. Les noms déclarés (noms de module, de réglage,
de déclencheur et de limite) sont du texte public et s'affichent tels quels.

Ces champs ne s'affichent que comme leur référence (`${NAME}`), ou comme « valeur littérale
configurée (masquée) », avec leur état défini/non défini. Cet état est évalué contre
l'environnement du processus de l'interface, qui est celui dont hérite un processus principal
lancé par l'interface.

Un réglage dont la valeur contient un tel champ, ou un secret noyé dans un texte plus long, n'est
jamais proposé comme un seul texte modifiable. Une liste ou une table se modifie entrée par entrée :
chaque entrée garde sa position, peut être supprimée, déplacée ou (lorsqu'elle est entièrement
affichée) modifiée, et une ligne ajoute une nouvelle entrée ; une entrée retenue s'affiche masquée
avec une case vide et un choix d'opération : « garder la valeur actuelle » (par défaut : elle est
gardée exactement, et un texte saisi avec elle est refusé) ou « remplacer par le texte saisi » (le
texte la remplace intégralement, et une case vide définit la chaîne vide). Un réglage texte unique
contenant un secret se comporte de la même façon, et un réglage texte non défini aussi ; celui que
porte la superposition peut en outre être vidé, ce qui le retire de la superposition et fait
revaloir la valeur de base. Seul ce qui a changé est appliqué à la configuration réelle chargée par
l'interface, de sorte que Contrôler et Enregistrer valident les mêmes valeurs. L'identité de chaque
entrée est tirée pour un rendu de la page, dans une session de navigateur, et n'est jamais délivrée
deux fois : toute entrée que la page affichée dans cette session n'a pas tirée de la configuration
courante (rendu antérieur, autre session ou autre processus d'interface, autre page, identité
altérée, ou entrée modifiée sur le disque depuis) est refusée comme page périmée (« rechargez la
page »), avec la même réponse sur Contrôler, Enregistrer et Supprimer, avant tout contrôle ou toute
écriture. Une soumission que l'interface ne peut pas appliquer exactement (entrée postée deux fois,
entrée à la fois supprimée et modifiée, nouvelle clé qui existe déjà) est refusée nommément et rien
n'est écrit. L'interface ne modifie jamais les variables d'environnement : définissez-les dans le
shell qui lance l'interface. Le jeton d'accès n'apparaît que dans l'unique ligne de démarrage sur
la sortie standard.

## 6. Appliquer : redémarrage et supervision

Il n'y a pas de rechargement à chaud : appliquer une modification est un **redémarrage supervisé**.

1. Appliquer lance d'abord un Contrôle de la configuration sur le disque (base + superposition).
   S'il échoue, l'application est refusée et rien n'est arrêté ni démarré.
2. L'interface arrête le processus principal **qu'elle a lancé elle-même** : terminaison, puis
   attente d'au plus le délai de grâce d'arrêt (10 s), puis mise à mort, puis attente d'au plus le
   délai post-mortem (5 s). Un processus encore vivant après cela reste supervisé (une application
   ultérieure ou l'arrêt de l'interface le signale à nouveau), et l'application est refusée avec un
   diagnostic nommant son pid : rien de nouveau n'est démarré à côté de lui.
3. Elle démarre l'argumentaire de lancement (§1) sans shell, avec la variable du fichier d'état
   (§8) pointée sur son chemin d'état.
4. Elle surveille le chemin d'état pendant une fenêtre bornée (**60 s** par défaut) et rapporte
   l'un de trois résultats :
   - **accepté** — un enregistrement d'état exploitable dont le `pid` est l'enfant qui vient d'être
     lancé, dont l'`state` est `ready` et dont le `digest` égale celui du disque ;
   - **refusé** — le processus s'est arrêté ; son code de sortie et ses diagnostics sans valeur
     sont affichés ;
   - **inconnu** — la fenêtre s'est écoulée sans ni l'un ni l'autre. Un enregistrement inexploitable
     ne compte jamais comme accepté.

L'interface ne supervise qu'un processus principal qu'elle a lancé (A4). Un processus principal
démarré ailleurs est signalé, étiqueté **« non supervisé »**, et jamais sollicité : arrêtez-le
vous-même avant d'appliquer, sinon deux processus principaux tourneront. Fermer l'interface arrête
l'enfant qu'elle a lancé.

## 7. Dérive : disque contre processus en cours

Chaque page indique si la configuration sur le disque et le processus en cours sont d'accord :

- **synchronisés** — un enregistrement d'état exploitable d'un processus vivant, dont l'empreinte
  égale celle des fichiers de base et de superposition courants ;
- **diffèrent** — un enregistrement exploitable d'un processus vivant avec une autre empreinte
  (vous avez enregistré depuis le dernier redémarrage : appliquez pour en tenir compte) ;
- **inconnu** — aucun enregistrement, un enregistrement inexploitable, ou un enregistrement dont le
  processus est mort.

Chaque page indique aussi l'état de l'enregistrement : « aucun enregistrement d'état », l'heure de
l'enregistrement, ou **« enregistrement d'état inexploitable »** avec une raison sans valeur (le
contrôle et le champ en échec, jamais le contenu du fichier). Un enregistrement inexploitable n'est
ni réparé ni partiellement pris en compte : sa dérive est inconnue et il n'apporte aucun état
d'exécution par module. Un module absent d'un enregistrement exploitable affiche « non rapporté par
le processus en cours ».

## 8. Le fichier d'état

Le processus principal publie son état dans un enregistrement d'état, un petit fichier JSON qu'il
remplace atomiquement lorsqu'il devient prêt et à chaque transition ultérieure d'un module vers
prêt ou dégradé. Il ne le fait que si la variable d'environnement `TWITCH_IA_COMPAGNON_STATUS_FILE`
est définie sur un chemin ; sans elle, le processus principal n'écrit rien et se comporte exactement
comme avant. L'enregistrement ne contient aucune valeur résolue ni aucun justificatif d'identité,
seulement une empreinte de la configuration non résolue.

L'interface surveille exactement un chemin d'état :

- `--status-file PATH` lorsqu'il est fourni ;
- sinon le chemin d'état par défaut : le nom complet du fichier de base suivi de `.status.json`,
  dans le dossier du fichier de base — `<nom du fichier de base>.status.json`, par exemple
  `config.yaml` → `config.yaml.status.json`.

L'interface définit `TWITCH_IA_COMPAGNON_STATUS_FILE` sur ce chemin pour chaque processus qu'elle
lance.

**Refus en cas de collision.** Le chemin d'état ne doit jamais être un fichier de configuration. Il
est comparé, en chemin absolu résolu (liens symboliques suivis, que le fichier existe déjà ou non),
au fichier de base et au chemin de superposition géré. En cas d'égalité, l'interface refuse de
démarrer — et `core.main` refuse de démarrer lorsque la variable nomme un tel chemin — avec le
diagnostic `status_path_collision` nommant le fichier de base ou de superposition, un code de sortie
non nul, et avant toute écoute, tout lancement ou toute écriture. Le défaut dérivé n'entre en
collision que lorsque la superposition s'appelle elle-même `<nom du fichier de base>.status.json`
(ou y mène par un lien) ; l'interface ne choisit alors jamais un autre chemin : passez un
`--status-file` sans collision.

**Rendre visible un processus principal démarré de l'extérieur.** Un processus principal que vous
démarrez vous-même (pas via Appliquer) n'est visible par l'interface que si vous définissez la
variable sur le chemin d'état de l'interface :

```sh
TWITCH_IA_COMPAGNON_STATUS_FILE=config.yaml.status.json \
    python -m core.main --config config.yaml
```

L'interface affiche alors sa dérive et son état d'exécution, étiqueté « non supervisé » (son pid
n'est pas l'enfant de l'interface), et ne le sollicite jamais.
