from debate_ratings.idnorm import canon, clean_display, name_tokens, signature


def test_canon_strips_nicknames_and_emoji():
    assert canon("Dylan 'Sleve' McCarthy") == "dylan mccarthy"
    assert canon("Adam Banihani 💻") == "adam banihani"
    assert canon('Jo "JoJo" Smith') == "jo smith"


def test_canon_keeps_apostrophe_surnames():
    assert canon("Sinead O'Nunain") == "sinead o'nunain"
    assert canon("Maria D'Souza") == "maria d'souza"


def test_clean_display_keeps_case():
    assert clean_display("JOHN Smith 3") == "JOHN Smith"
    assert clean_display("  Ana (Annie) Lopez ") == "Ana Lopez"


def test_signature_folds_accents_and_spacing():
    assert signature(canon("José Mc Carthy")) == signature(canon("Jose McCarthy"))


def test_name_tokens_glues_particles():
    assert name_tokens("dylan mc carthy") == ["dylan", "mccarthy"]
    assert name_tokens("sinead o nunain") == ["sinead", "onunain"]
