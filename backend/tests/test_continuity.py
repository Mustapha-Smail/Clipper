from clipper.studio import continuity
from clipper.studio import script as script_mod


def shot(start, *lines, place="", note=""):
    return {"start_word": start, "note": note, "camera": "wide", "page": False, "cut": False,
            "place": place, "lines": list(lines)}


def words(n=50):
    return [{"w": f"w{i}", "s": i * 0.4, "e": i * 0.4 + 0.3, "section": "a"} for i in range(n)]


def test_duplicate_names_conflicting_ids_and_reference_before_definition_are_reported():
    shots = [
        shot(0, "0 actor ada left", "1 who ada \"Ada Lovelace\" robe"),
        shot(10, "10 who countess \"Ada Lovelace\" crown", "11 who ada \"Ada Lovelace\" toga"),
    ]
    lines = continuity.check("ink", shots, words())
    assert any("multiple person IDs" in line and "ada" in line and "countess" in line for line in lines)
    assert any("ada' is redefined" in line for line in lines)
    assert any("referenced before" in line for line in lines)


def test_valid_recurring_cast_and_object_ids_have_no_continuity_advice():
    shots = [
        shot(0, '0 who ada "Ada Lovelace" robe', "1 actor ada left", "2 draw engine right as machine",
             place="study", note="Ada meets the engine"),
        shot(15, "15 actor ada center", "16 move machine left", place="workshop", note="The engine moves"),
    ]
    assert continuity.check("ink", shots, words()) == []


def test_object_replacement_setting_churn_and_missing_callback_are_advisory():
    sections = [{"key": "a", "heading": "A", "text": "One. Two. Three. Four."}]
    stored = {"hash": script_mod.text_hash(sections), "roles": [
        {"n": 1, "role": "hook"}, {"n": 2, "role": "fact"}, {"n": 3, "role": "fact"},
        {"n": 4, "role": "payoff"}], "topic": "one"}
    spoken = [{"w": word, "s": i * 0.5, "e": i * 0.5 + 0.4, "section": "a"}
              for i, word in enumerate("One. Two. Three. Four.".split())]
    shots = [shot(0, "0 draw key left as first_key", place="room"),
             shot(1, "1 actor narrator", place="hall"),
             shot(2, "2 draw key right as second_key", place="room"),
             shot(3, "3 actor narrator", place="street")]
    story_map = {"ending": {"callback": "the red ribbon returns"}}
    lines = continuity.check("ink", shots, spoken, sections, stored, story_map)
    assert any("Recurring object 'key'" in line for line in lines)
    assert any("room → hall → room" in line for line in lines)
    assert any("red ribbon" in line for line in lines)


def test_unknown_styles_are_left_alone():
    assert continuity.check("flat", [{"start_word": 0}]) == []
    assert continuity.check("film", [{"start_word": 0, "lines": []}]) == []
