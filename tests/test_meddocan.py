from harness.datasets.meddocan import _canonical, load_meddocan


def test_meddocan_mapping_policy():
    assert _canonical("TERRITORIO", "28036") == "ZIP" and _canonical("TERRITORIO", "Madrid") == "CITY"
    assert _canonical("FAMILIARES_SUJETO_ASISTENCIA", "madre") is None
    assert _canonical("FAMILIARES_SUJETO_ASISTENCIA", "María") == "RELATIVE_NAME"
    assert _canonical("PAIS", "España") is None and _canonical("EDAD_SUJETO_ASISTENCIA", "70 años") is None
    assert _canonical("ID_ASEGURAMIENTO", "26 63514095 04") == "MEMBER_ID"


def test_meddocan_brat_parsing(tmp_path):
    d = tmp_path / "test" / "brat"
    d.mkdir(parents=True)
    text = "Paciente Ana López, NHC 123456, Madrid. Edad 70 años."
    (d / "doc1.txt").write_text(text, encoding="utf-8")
    (d / "doc1.ann").write_text("T1\tNOMBRE_SUJETO_ASISTENCIA 9 18\tAna López\nT2\tID_SUJETO_ASISTENCIA 24 30\t123456\n"
                                "T3\tTERRITORIO 32 38\tMadrid\nT4\tEDAD_SUJETO_ASISTENCIA 45 52\t70 años\n", encoding="utf-8")
    ds = load_meddocan("meddocan_test", tmp_path)
    s = ds.samples[0]
    assert [(e.text, e.label) for e in s.entities] == [("Ana López", "NAME"), ("123456", "MRN"), ("Madrid", "CITY")]
    assert len(s.ignore_spans) == 1 and s.metadata["registry"]["simulated_from_gold"]
