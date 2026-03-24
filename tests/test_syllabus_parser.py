from backend.app.services.syllabus_parser import parse_subjects_and_topics
from backend.app.services.topic_text import humanize_topic_text, looks_like_reference_text


def test_classic_course_block_parser_uses_previous_line_as_course_title():
    text = """
    Information Retrieval Systems
    AI622PE
    Professional Elective-II
    Unit-I
    Information Retrieval Systems & Capabilities
    Unit-V
    Text Search Algorithms & MIR
    """

    parsed = parse_subjects_and_topics(text)
    parsed_by_name = {item["name"]: item["topics"] for item in parsed}

    assert "AI622PE - Information Retrieval Systems" in parsed_by_name
    assert "Unit I: Information Retrieval Systems & Capabilities" in parsed_by_name[
        "AI622PE - Information Retrieval Systems"
    ]
    assert "Unit V: Text Search Algorithms & MIR" in parsed_by_name[
        "AI622PE - Information Retrieval Systems"
    ]


def test_classic_course_block_parser_keeps_unit_titles_as_topics():
    text = """
    SM601MS
    Business Economics and Financial Analysis
    Unit-I
    Introduction To Business And Economics
    Unit-IV
    Financial Accounting
    Unit-V
    Financial Analysis Through Ratios
    """

    parsed = parse_subjects_and_topics(text)
    parsed_by_name = {item["name"]: item["topics"] for item in parsed}

    assert "SM601MS - Business Economics and Financial Analysis" in parsed_by_name
    assert "Profit Analysis" not in parsed_by_name
    assert "Unit I: Introduction To Business And Economics" in parsed_by_name[
        "SM601MS - Business Economics and Financial Analysis"
    ]
    assert "Unit IV: Financial Accounting" in parsed_by_name[
        "SM601MS - Business Economics and Financial Analysis"
    ]


def test_classic_course_block_parser_stops_at_single_letter_course_codes():
    text = """
    Information Retrieval Systems
    AI622PE
    Professional Elective-II
    Unit-I
    Information Retrieval Systems & Capabilities
    Unit-V
    Text Search Algorithms & MIR
    C601OE
    Fundamentals of Internet of Things
    Unit-I
    Introduction to Internet of Things
    """

    parsed = parse_subjects_and_topics(text)
    parsed_by_name = {item["name"]: item["topics"] for item in parsed}

    assert "AI622PE - Information Retrieval Systems" in parsed_by_name
    assert "C601OE - Fundamentals of Internet of Things" in parsed_by_name
    assert "Unit I: Introduction to Internet of Things" not in parsed_by_name[
        "AI622PE - Information Retrieval Systems"
    ]


def test_classic_course_block_parser_stops_when_units_wrap_back_to_one():
    text = """
    Information Retrieval Systems
    AI622PE
    Professional Elective-II
    Unit-I
    Information Retrieval Systems & Capabilities
    Unit-V
    Text Search Algorithms & MIR
    Unit-I
    Introduction to Internet of Things
    """

    parsed = parse_subjects_and_topics(text)
    parsed_by_name = {item["name"]: item["topics"] for item in parsed}

    assert "AI622PE - Information Retrieval Systems" in parsed_by_name
    assert "Unit I: Introduction to Internet of Things" not in parsed_by_name[
        "AI622PE - Information Retrieval Systems"
    ]


def test_classic_course_block_parser_keeps_subtopics_inside_unit_blocks():
    text = """
    AI602PC
    Data Analytics
    UNIT-I
    Data Management
    sources of Data like Sensors/Signals/GPS etc.
    Missing Values, Duplicate data, and Data Processing
    UNIT-II
    Data Analytics
    Introduction to Analytics
    """

    parsed = parse_subjects_and_topics(text)
    parsed_by_name = {item["name"]: item["topics"] for item in parsed}

    assert "AI602PC - Data Analytics" in parsed_by_name
    topics = parsed_by_name["AI602PC - Data Analytics"]
    assert "Unit I: Data Management" in topics
    assert "Unit I: sources of Data like Sensors/Signals/GPS etc." in topics
    assert "Unit I: Missing Values, Duplicate data, and Data Processing" in topics
    assert "Unit II: Introduction to Analytics" in topics


def test_humanize_topic_text_repairs_generic_ocr_fragmentation():
    cleaned = humanize_topic_text("Unit 2: His torical constra ints and Tur ing mach ine")

    assert "Historical" in cleaned
    assert "constraints" in cleaned
    assert "Turing" in cleaned


def test_reference_detection_uses_citation_structure_not_named_publishers():
    assert looks_like_reference_text("J. P. Tremblay, R. Manohar, 2nd ed., 2008.")
    assert not looks_like_reference_text("Unit 2: Parse Trees and ambiguity")
