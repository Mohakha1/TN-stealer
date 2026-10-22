# language: Python 3.11, file: bot.py, target: Discord (discord.py 2.x)
# entry: python bot.py  —  needs .env with BOT_TOKEN + ADMIN_ROLE_ID + KEY_ROLE_ID

import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
from dotenv import load_dotenv
import database as db

load_dotenv()

TOKEN        = os.getenv("BOT_TOKEN")
ADMIN_ROLE   = int(os.getenv("ADMIN_ROLE_ID", "0"))
KEY_ROLE     = int(os.getenv("KEY_ROLE_ID",   "0"))   # role given on key redeem
PREFIX       = os.getenv("PREFIX", "!")
GUILD_ID     = int(os.getenv("GUILD_ID", "0"))        # 0 = global slash cmds (slow sync)

# ── setup ────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)
tree = bot.tree


def is_admin(inter: discord.Interaction) -> bool:
    if inter.user.guild_permissions.administrator:
        return True
    return any(r.id == ADMIN_ROLE for r in inter.user.roles)


def fmt_duration(h: int | None) -> str:
    if h is None:
        return "دائم ♾️"
    if h < 24:
        return f"{h} ساعة"
    if h < 168:
        d = h // 24
        return f"{d} يوم"
    if h < 720:
        w = h // 168
        return f"{w} أسبوع"
    m = h // 720
    return f"{m} شهر"


def fmt_remaining(td: datetime.timedelta | None) -> str:
    if td is None:
        return "دائم ♾️"
    total = int(td.total_seconds())
    if total <= 0:
        return "منتهي"
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, _   = divmod(rem, 60)
    parts  = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    return " ".join(parts) or "<1m"


# ── events ───────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    db.init_db()
    expire_check.start()
    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    else:
        await tree.sync()
    print(f"[TN KeyBot] online as {bot.user}  |  prefix={PREFIX}")


# ── background task: expire keys & remove roles ──────────────────────────────

@tasks.loop(minutes=10)
async def expire_check():
    import sqlite3, datetime as _dt
    now = _dt.datetime.utcnow().isoformat()
    with sqlite3.connect(db.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        expired = c.execute(
            "SELECT key, redeemed_by FROM keys WHERE is_active=1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,)
        ).fetchall()
        for row in expired:
            c.execute("UPDATE keys SET is_active=0 WHERE key=?", (row["key"],))
            if row["redeemed_by"] and KEY_ROLE:
                for guild in bot.guilds:
                    member = guild.get_member(int(row["redeemed_by"]))
                    if member:
                        role = guild.get_role(KEY_ROLE)
                        if role and role in member.roles:
                            await member.remove_roles(role, reason="Key expired")


# ═══════════════════════════════════════════════════════════════════════════
#  ADMIN SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@tree.command(name="genkey", description="[ADMIN] اصدر كي جديد")
@app_commands.describe(
    duration="المدة: 1h / 12h / 1d / 7d / 30d / 1w / perm",
    note="ملاحظة اختيارية (يوتيوب، منصة، إلخ)"
)
async def genkey(inter: discord.Interaction, duration: str, note: str = ""):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return

    res = db.generate_key(duration, note, str(inter.user.id))
    if not res["ok"]:
        await inter.response.send_message(f"❌ {res['error']}", ephemeral=True)
        return

    embed = discord.Embed(title="🔑 كي جديد", color=0x00ff88)
    embed.add_field(name="الكي",   value=f"```{res['key']}```", inline=False)
    embed.add_field(name="المدة",  value=fmt_duration(res["duration_h"]),  inline=True)
    if res["expires_at"]:
        exp = datetime.datetime.fromisoformat(res["expires_at"])
        embed.add_field(name="ينتهي",  value=discord.utils.format_dt(exp, "R"), inline=True)
    if note:
        embed.add_field(name="ملاحظة", value=note, inline=False)
    embed.set_footer(text=f"صادر من {inter.user}")
    await inter.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="bulkgen", description="[ADMIN] اصدر عدة كيات دفعة واحدة")
@app_commands.describe(
    amount="عدد الكيات (1-50)",
    duration="المدة: 1h / 1d / 7d / 30d / perm",
    note="ملاحظة"
)
async def bulkgen(inter: discord.Interaction, amount: int, duration: str, note: str = ""):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return
    if not 1 <= amount <= 50:
        await inter.response.send_message("❌ بين 1 و 50 كي بس", ephemeral=True)
        return

    await inter.response.defer(ephemeral=True)
    lines = []
    for _ in range(amount):
        res = db.generate_key(duration, note, str(inter.user.id))
        if res["ok"]:
            lines.append(res["key"])

    block = "\n".join(lines)
    embed = discord.Embed(title=f"🔑 {len(lines)} كيات — {fmt_duration(db._parse_duration(duration))}", color=0x00ff88)
    if len(block) <= 1000:
        embed.add_field(name="الكيات", value=f"```{block}```", inline=False)
    else:
        # send as file
        import io
        f = discord.File(io.BytesIO(block.encode()), filename="keys.txt")
        await inter.followup.send(embed=embed, file=f, ephemeral=True)
        return
    await inter.followup.send(embed=embed, ephemeral=True)


@tree.command(name="revokekey", description="[ADMIN] ألغِ كي")
@app_commands.describe(key="الكي المراد إلغاؤه")
async def revokekey(inter: discord.Interaction, key: str):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return

    info = db.check_key_info(key)
    ok   = db.revoke_key(key)
    if not ok:
        await inter.response.send_message("❌ الكي مش موجود", ephemeral=True)
        return

    # strip role if redeemed
    if info and info["redeemed_by"] and KEY_ROLE:
        for guild in bot.guilds:
            member = guild.get_member(int(info["redeemed_by"]))
            if member:
                role = guild.get_role(KEY_ROLE)
                if role and role in member.roles:
                    await member.remove_roles(role, reason="Key revoked by admin")

    await inter.response.send_message(f"✅ الكي `{key.upper()}` اتلغى", ephemeral=True)


@tree.command(name="keyinfo", description="[ADMIN] معلومات كي")
@app_commands.describe(key="الكي")
async def keyinfo(inter: discord.Interaction, key: str):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return

    info = db.check_key_info(key)
    if not info:
        await inter.response.send_message("❌ الكي مش موجود", ephemeral=True)
        return

    color = 0x00ff88 if info["is_active"] else 0xff4444
    embed = discord.Embed(title="🔍 معلومات الكي", color=color)
    embed.add_field(name="الكي",    value=f"```{info['key']}```", inline=False)
    embed.add_field(name="الحالة", value="✅ فعال" if info["is_active"] else "❌ ملغى/منتهي", inline=True)
    embed.add_field(name="المدة",  value=fmt_duration(info["duration_h"]), inline=True)

    if info["expires_at"]:
        exp = datetime.datetime.fromisoformat(info["expires_at"])
        embed.add_field(name="ينتهي", value=discord.utils.format_dt(exp, "F"), inline=True)

    if info["redeemed_by"]:
        embed.add_field(name="مستخدم من",  value=f"<@{info['redeemed_by']}>", inline=True)
        if info["redeemed_at"]:
            ra = datetime.datetime.fromisoformat(info["redeemed_at"])
            embed.add_field(name="وقت الاستخدام", value=discord.utils.format_dt(ra, "R"), inline=True)
    else:
        embed.add_field(name="الحالة", value="لم يُستخدم بعد", inline=True)

    if info["note"]:
        embed.add_field(name="ملاحظة", value=info["note"], inline=False)

    await inter.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="listkeys", description="[ADMIN] قائمة آخر الكيات")
@app_commands.describe(
    amount="عدد الكيات (افتراضي 15)",
    active_only="فعالة فقط؟"
)
async def listkeys(inter: discord.Interaction, amount: int = 15, active_only: bool = False):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return

    await inter.response.defer(ephemeral=True)
    keys = db.list_keys(limit=min(amount, 50), active_only=active_only)
    if not keys:
        await inter.followup.send("لا يوجد كيات", ephemeral=True)
        return

    lines = []
    for k in keys:
        status = "✅" if k["is_active"] else "❌"
        user   = f"<@{k['redeemed_by']}>" if k["redeemed_by"] else "—"
        dur    = fmt_duration(k["duration_h"])
        lines.append(f"{status} `{k['key']}` | {dur} | {user}")

    embed = discord.Embed(title=f"🗝️ {len(keys)} كيات", description="\n".join(lines), color=0x5865F2)
    await inter.followup.send(embed=embed, ephemeral=True)


@tree.command(name="resethwid", description="[ADMIN] ريست HWID لعضو")
@app_commands.describe(member="العضو")
async def resethwid(inter: discord.Interaction, member: discord.Member):
    if not is_admin(inter):
        await inter.response.send_message("❌ ما عندك صلاحية", ephemeral=True)
        return
    ok = db.reset_hwid(str(member.id))
    msg = f"✅ HWID اتمسح لـ {member.mention}" if ok else f"ℹ️ ما في HWID مسجل لـ {member.mention}"
    await inter.response.send_message(msg, ephemeral=True)


# ═══════════════════════════════════════════════════════════════════════════
#  USER SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════════════════

@tree.command(name="redeem", description="فعّل كيك هنا")
@app_commands.describe(key="الكي")
async def redeem(inter: discord.Interaction, key: str):
    res = db.redeem_key(key, str(inter.user.id))

    if not res["ok"]:
        await inter.response.send_message(f"❌ {res['error']}", ephemeral=True)
        return

    # assign role
    if KEY_ROLE and inter.guild:
        role = inter.guild.get_role(KEY_ROLE)
        if role:
            await inter.user.add_roles(role, reason="Key redeemed")

    embed = discord.Embed(title="✅ الكي اتفعّل!", color=0x00ff88)
    embed.add_field(name="الكي",    value=f"||`{res['key']}`||", inline=False)
    embed.add_field(name="المدة",   value=fmt_duration(res["duration_h"]), inline=True)
    if res["expires_at"]:
        exp = datetime.datetime.fromisoformat(res["expires_at"])
        embed.add_field(name="ينتهي", value=discord.utils.format_dt(exp, "R"), inline=True)
    embed.set_footer(text="TN Executor — استمتع 🔥")
    await inter.response.send_message(embed=embed, ephemeral=True)


@tree.command(name="mystatus", description="شوف حالة كيك")
async def mystatus(inter: discord.Interaction):
    res = db.check_user_key(str(inter.user.id))
    if not res["ok"]:
        embed = discord.Embed(
            title="❌ ما عندك كي فعّال",
            description="استخدم `/redeem <key>` لتفعيل كيك",
            color=0xff4444
        )
        await inter.response.send_message(embed=embed, ephemeral=True)
        return

    embed = discord.Embed(title="🔑 كيك فعّال", color=0x00ff88)
    embed.add_field(name="الكي",       value=f"||`{res['key']}`||", inline=False)
    embed.add_field(name="الوقت المتبقي", value=fmt_remaining(res["remaining"]), inline=True)
    if res["expires_at"]:
        exp = datetime.datetime.fromisoformat(res["expires_at"])
        embed.add_field(name="ينتهي",  value=discord.utils.format_dt(exp, "F"), inline=True)
    await inter.response.send_message(embed=embed, ephemeral=True)


# ── HTTP verify endpoint (optional, for your Lua script) ─────────────────────
# your executor script can hit:  POST /verify  body: {"key":"TN-...", "hwid":"..."}
# run this alongside the bot with:  python api.py

# ── run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    bot.run(TOKEN)
