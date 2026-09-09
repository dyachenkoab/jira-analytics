import gettext
import locale
import os
from pathlib import Path


def get_system_locale() -> str:
    lang = os.environ.get("LANG", "")
    if lang:
        return lang.split(".")[0]
    lang, _ = locale.getdefaultlocale()
    return lang if lang else "en_US"


def get_translations_path(file_path: str) -> str:
    local = Path(file_path).resolve().parent / "translations"
    if local.exists():
        return str(local)
    return "/usr/share/jira-analytics/translations"


class TranslatorBase:
    __locale = "en_US"

    def __init__(self, domain: str, localedir: str, locale: str | None = None) -> None:
        if locale is None:
            locale = get_system_locale()
        self.domain = domain
        self.localedir = localedir
        self.__class__.__locale = locale
        self.translation = gettext.translation(
            self.domain, self.localedir, languages=[locale], fallback=True
        )

    @classmethod
    def __set_locale(cls, locale: str) -> None:
        cls.__locale = locale

    @classmethod
    def get_locale(cls) -> str:
        return cls.__locale

    def change_locale(self, locale: str) -> None:
        self.__set_locale(locale)
        self.translation = gettext.translation(
            self.domain, self.localedir, languages=[locale], fallback=True
        )


class GettextTranslator(TranslatorBase):
    def __init__(self, domain: str, localedir: str, locale: str | None = None) -> None:
        TranslatorBase.__init__(self, domain, localedir, locale)

    def gettext(self, msg: str) -> str:
        return self.translation.gettext(msg)

    def pgettext(self, context: str, msg: str) -> str:
        try:
            return self.translation.pgettext(context, msg)
        except AttributeError:
            return self.translation.gettext(msg)

    def ngettext(self, msgid1: str, msgid2: str, n: int) -> str:
        return self.translation.ngettext(msgid1, msgid2, n)

    def npgettext(self, context: str, msgid1: str, msgid2: str, n: int) -> str:
        try:
            return self.translation.npgettext(context, msgid1, msgid2, n)
        except AttributeError:
            return self.translation.ngettext(msgid1, msgid2, n)


_module_translator = GettextTranslator("jira_qt", get_translations_path(__file__))

_ = _module_translator.gettext
_c = _module_translator.pgettext
ngettext = _module_translator.ngettext
npgettext = _module_translator.npgettext


try:
    from PyQt5.QtCore import QTranslator

    class GettextTranslatorQt(QTranslator):
        def __init__(self, domain: str, localedir: str, locale: str | None = None) -> None:
            QTranslator.__init__(self)
            self._gettext = GettextTranslator(domain, localedir, locale)

        def translate(self, context: str, sourceText: str, disambiguation: str | None = None, n: int = -1) -> str:
            try:
                translated = self._gettext.translation.pgettext(context, sourceText)
                if translated == sourceText and context and not context.endswith(".qml"):
                    return self._gettext.translation.pgettext(f"{context}.qml", sourceText)
                return translated
            except AttributeError:
                return self._gettext.translation.gettext(sourceText)
except ImportError:
    pass
