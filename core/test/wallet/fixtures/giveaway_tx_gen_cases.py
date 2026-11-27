import json, subprocess, sys
import os
T=os.environ.get("PARITY_DIR", "/tmp/giveaway-parity")  # the throwaway crate dir; see core/src/wallet/README.md
v=json.load(open(f"{T}/hands/tests/giveaway/fixtures/rewards_tx.json"))
base=dict(program_id=v["program_id"],mint=v["mint"],token_program=v["token_program"],authority=v["authority"],merkle_root=v["merkle_root"],total_amount=int(v["total_amount"]),clawback_ts=v["clawback_ts"],recent_blockhash=v["recent_blockhash"],seed_secret_hex=v["seed_secret_hex"])
close_base=dict(program_id=v["program_id"],mint=v["mint"],token_program=v["token_program"],authority=v["authority"],distribution_seed=v["seed_pubkey"],distributor=v["distribution"],recent_blockhash=v["recent_blockhash"])
cases=[]
def add(name,op,req,raw=None):
    cases.append({"name":name,"op":op,"stdin":raw if raw is not None else json.dumps(req)})
add("create_vector","create",base)
add("close_vector","close",close_base)
b2=dict(base, mint="4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU", token_program="TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", authority="4Nd1mBQtrMJVYVfKf2PJy9NZUZdTAsp7D4xWLs4gDB4T",
        merkle_root="ab"*32, seed_secret_hex="0a"*32, recent_blockhash="EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG")
b2["total_amount"]=18446744073709551615
b2["clawback_ts"]=9223372036854775807
add("create_u64_max","create",b2)
add("create_minimum","create",dict(b2, total_amount=1, clawback_ts=1, seed_secret_hex="ff"*32, merkle_root="00"*32))
add("create_uppercase_root","create",dict(base, merkle_root=base["merkle_root"].upper()))
add("err_program_not_base58","create",dict(base, program_id="0OIl"))
add("err_program_short","create",dict(base, program_id="11111111"))
add("err_mint_empty","create",dict(base, mint=""))
add("err_authority_bad","create",dict(base, authority="not base58 !"))
add("err_blockhash_short","create",dict(base, recent_blockhash="3Bxs"))
add("err_root_short","create",dict(base, merkle_root="abcd"))
add("err_root_nonhex","create",dict(base, merkle_root="zz"*32))
add("err_total_zero","create",dict(base, total_amount=0))
add("err_clawback_zero","create",dict(base, clawback_ts=0))
add("err_clawback_negative","create",dict(base, clawback_ts=-5))
add("err_seed_short","create",dict(base, seed_secret_hex="abcd"))
add("err_close_distributor","close",dict(close_base, distributor=v["vault"]))
add("err_close_seed_bad","close",dict(close_base, distribution_seed="xx"))
add("err_unknown_op","send",{})
add("err_empty_op","",{})
add("err_unknown_field","create",dict(base, extra=1))
add("err_close_unknown_field","close",dict(close_base, seed_secret_hex="00"*32))
add("err_missing_field","create",{k:x for k,x in base.items() if k!="mint"})
add("err_close_missing_field","close",{k:x for k,x in close_base.items() if k!="distributor"})
add("err_total_string","create",None,raw=json.dumps(dict(base,total_amount="5")))
add("err_total_float","create",None,raw=json.dumps(base).replace(str(base["total_amount"]),"1.5"))
add("err_total_negative","create",None,raw=json.dumps(base).replace(str(base["total_amount"]),"-1"))
add("err_total_overflow","create",None,raw=json.dumps(base).replace(str(base["total_amount"]),"18446744073709551616"))
add("err_mint_number","create",dict(base, mint=5))
add("err_not_json","create",None,raw="{not json")
add("err_empty_input","create",None,raw="")
add("err_array","create",None,raw="[1,2]")
add("err_seed_null","create",dict(base, seed_secret_hex=None))
out=[]
for c in cases:
    p=subprocess.run([f"{T}/spine/target/release/giveaway-parity",c["op"]],input=c["stdin"],capture_output=True,text=True)
    out.append({**c,"code":p.returncode,"stdout":p.stdout,"stderr":p.stderr})
os.makedirs(f"{T}/cases", exist_ok=True)
json.dump(out,open(f"{T}/cases/results.json","w"),indent=1)
for r in out: print(r["name"],r["code"],(r["stderr"] or r["stdout"])[:170].strip())
