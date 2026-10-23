"""Reading the game's log for a server logout."""

from wiz101_auto.gamelog import LogWatch, disconnect_line

AFK = (
    "10/22/26 17:50:31 [DBGM] GameClient      AppCloseConnection() called, connection state is 6\n"
    "10/22/26 17:50:31 [DBGM] CORE_SEER       "
    "GameClient::MSG_Disconnect_Afk :  Logout due to Away From Keyboard\n"
)


def test_a_zone_change_is_not_a_logout_but_the_afk_kick_is():
    zone_change = "10/22/26 17:00:41 [DBGM] GameClient  AppCloseConnection() called, connection state is 16"
    assert disconnect_line(zone_change) is None
    assert "MSG_Disconnect_Afk" in disconnect_line(AFK)


def test_only_lines_logged_after_the_watch_starts_count(tmp_path):
    log = tmp_path / "WizardClient.log"
    log.write_text(AFK)  # an old logout, before this run
    watch = LogWatch(log)
    assert watch.poll() is None
    with log.open("a") as f:
        f.write("10/22/26 18:00:00 [STAT] ClientDuel      Duel 1: MSG_CombatPips()\n")
    assert watch.poll() is None
    with log.open("a") as f:
        f.write(AFK)
    assert "Away From Keyboard" in watch.poll()
    assert watch.poll() is None


def test_a_relaunched_game_rewrites_its_log(tmp_path):
    log = tmp_path / "WizardClient.log"
    log.write_text("x" * 5000)
    watch = LogWatch(log)
    log.write_text(AFK)  # shorter: the game started again
    assert watch.poll() is not None


def test_no_log_is_no_logout(tmp_path):
    assert LogWatch(tmp_path / "missing.log").poll() is None
