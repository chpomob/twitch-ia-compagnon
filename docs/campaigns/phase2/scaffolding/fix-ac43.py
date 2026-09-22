import pathlib, re

# ---- 1. AC43 : ajouter le cas "injoignable au prepare" ----
sp = pathlib.Path("docs/campaigns/phase2/spec.md"); s = sp.read_text()
old = """  enables the audio modules, the speech endpoint setting is EMPTY:
  `audio.speak` is not in the bound action set, the runtime's capability
  view names it with a value-free not-ready reason, no request is
  attempted and startup succeeds;"""
new = """  enables the audio modules, the speech endpoint setting is EMPTY, and
  the same holds when a configured service is UNREACHABLE at prepare:
  `audio.speak` is not in the bound action set, the runtime's capability
  view names it with a value-free not-ready reason (empty and unreachable
  share the not-ready shape, with distinct value-free reasons), no
  request is attempted and startup succeeds;"""
assert old in s, "ancre AC43 absente"
s = s.replace(old, new, 1)
sp.write_text(s); print("1. AC43 : cas injoignable ajoute")

# ---- 2 et 3. plan.md P19 ----
pp = pathlib.Path("docs/campaigns/phase2/plan.md"); p = pp.read_text()
a = "and the chat-only profile is identical (arbiter decision 22/09: no provider default)."
b = ("and the chat-only profile is identical; with an endpoint CONFIGURED but UNREACHABLE at prepare, the same "
     "non-ready naming appears with zero requests and startup still succeeds (arbiter decision 22/09: no provider default).")
assert a in p, "ancre Tests AC43 absente"
p = p.replace(a, b, 1)

c = "and then with placeholder values exported (the bound path, 0 sockets opened, exit 0);"
d = ("and then with placeholder values exported, settings present, so `--check-config` validates their SHAPE only "
     "and still opens 0 sockets and exits 0 (readiness is decided at prepare and is NOT asserted by this check);")
assert c in p, "ancre check-config absente"
p = p.replace(c, d, 1)
pp.write_text(p); print("2-3. P19 : cas injoignable, formulation check-config corrigee")

# ---- 4. P20 : l'essai reel suppose une configuration operateur ----
e = "New, opt-in by environment variables and skipped with a reason otherwise: real integration trials"
f = ("New, opt-in by environment variables and skipped with a reason otherwise, the trial requires the OPERATOR to "
     "configure the provider, never a shipped default (AC43): real integration trials")
if e in p:
    p = p.replace(e, f, 1); pp.write_text(p); print("4. P20 : l'essai suppose une config operateur")
else:
    print("4. P20 : formulation deja differente, verifier manuellement")
