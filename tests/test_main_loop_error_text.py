"""The main loop logs the exception type and traceback even when str(ex) is empty."""
import app


def test_empty_message_still_names_type_and_frame():
    def boom():
        raise KeyError()  # str(KeyError()) == ''
    try:
        boom()
    except Exception as ex:
        txt = app.loop_error_text(ex)
    assert txt.startswith('main loop: KeyError: ')
    assert 'boom' in txt and 'Traceback' in txt
