from myutils.utils import (
    APIType,
    common_list_models,
    common_create_gemini_request,
    common_parse_normal_response,
    common_create_gpt_data,
    dynamiccishuname,
)
import NativeUtils
from myutils.proxy import getproxy
from myutils.config import globalconfig
from cishu.cishubase import cishubase
from translator.gptcommon import createheaders
from gui.customparams import customparams, getcustombodyheaders
from language import Languages
from qtsymbols import *
from gui.usefulwidget import SuperCombo
from html.parser import HTMLParser
from html import unescape
from traceback import print_exc
import gobject
import re
import threading


def list_models(typename, regist):
    return common_list_models(
        getproxy(("cishu", typename)),
        APIType(regist["API接口地址"]()),
        regist["SECRET_KEY"]().split("|")[0],
    )


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._parts.append(data)

    def handle_entityref(self, name):
        if not self._skip:
            self._parts.append(unescape("&%s;" % name))

    def handle_charref(self, name):
        if not self._skip:
            self._parts.append(unescape("&#%s;" % name))

    def text(self):
        return "".join(self._parts)


def html_to_plain_text(html: str) -> str:
    if not html:
        return ""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except:
        print_exc()
        return re.sub(r"<[^>]+>", "", html)
    text = unescape(parser.text())
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _enabled_ref_dicts(self_id="chatgptlike"):
    ids = []
    names = []
    seen = set()
    ordered = list(globalconfig.get("cishuvisrank") or [])
    for k in list(globalconfig.get("cishu") or {}):
        if k not in ordered:
            ordered.append(k)
    for k in ordered:
        if k == self_id or k in seen:
            continue
        info = globalconfig["cishu"].get(k)
        if not info or not info.get("use"):
            continue
        base = getattr(gobject, "base", None)
        loaded = getattr(base, "cishus", None) if base is not None else None
        if loaded is not None and k not in loaded:
            continue
        seen.add(k)
        ids.append(k)
        names.append(dynamiccishuname(k))
    return names, ids


class ref_dict_combo(QWidget):
    def __init__(self, dd: dict, key="ref_dict"):
        super().__init__()
        self._key = key
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = SuperCombo()
        names, ids = _enabled_ref_dicts()
        if not names:
            names, ids = ["无可用辞书"], [""]
        self.combo.addItems(names, ids)
        current = dd.get(key, "")
        if current in ids:
            self.combo.setCurrentData(current)
        lay.addWidget(self.combo)

    def updateValues(self):
        return {self._key: self.combo.getCurrentData() or ""}


_ref_query_guard = threading.local()


class chatgptlike(cishubase):
    use_github_md_css = True
    backgroundparser = 'document.querySelector("#luna_dict_internal_view > article").style.backgroundColor="rgba(0,0,0,0)"'

    def init(self):
        self.maybeuse = {}

    def langmap(self):
        return Languages.createenglishlangmap()

    def result_cache_key(self, word, sentence=None):
        __ = {}
        __.update(self.rawconfig)
        if "modellistcache" in __:
            __.pop("modellistcache")
        refkey = None
        if self.config.get("use_ref_dict"):
            dict_id = self.config.get("ref_dict") or ""
            cishu = getattr(getattr(gobject, "base", None), "cishus", {}).get(dict_id)
            if cishu:
                try:
                    refkey = cishu.result_cache_key(word, sentence)
                except:
                    print_exc()
        return (word, sentence, str(__), refkey)

    def search_1(self, apitype: APIType, sysprompt, query, extrabody, extraheader):
        message = [{"role": "system", "content": sysprompt}]
        message.append({"role": "user", "content": query})
        headers = createheaders(
            apitype,
            self.multiapikeycurrent["SECRET_KEY"],
            self.maybeuse,
            self.proxy,
            extraheader,
        )
        _json = common_create_gpt_data(self.config, message, extrabody)
        response = self.proxysession.post(
            apitype.finalurl(), headers=headers, json=_json
        )
        return response

    def _gptlike_createsys(self, usekey, tempk):
        default = "You are a professional dictionary assistant whose task is to help users search for information such as the meaning, pronunciation, etymology, synonyms, antonyms, and example sentences of {srclang} words. \nYou should be able to handle queries in multiple languages and provide in-depth information or simple definitions according to user needs. You should reply in {tgtlang}.\nThe user may provide the sentence in which the word is located. If the user provides the sentence in which the word is located, the semantics of the word in the sentence should also be analyzed.\nThe user may also provide dictionary reference text. If provided, use it as the primary reference for pronunciation, pitch accent, and definitions, but it may be incomplete or incorrect, so do not follow it blindly."
        template = self.config[tempk] if self.config[usekey] else None
        template = template if template else default
        template = self.smartparselangprompt(template)
        return template

    def _gptlike_createquery(self, query, sentence, usekey, tempk, dictref=None):
        user_prompt = (
            self.config.get(tempk, "") if self.config.get(usekey, False) else ""
        )
        user_prompt = user_prompt.lstrip()
        if "{word}" not in user_prompt:
            user_prompt += "{word}"
        user_prompt = user_prompt.replace("{word}", query)
        if sentence:
            if "{sentence}" not in user_prompt:
                user_prompt = "sentence: {sentence}\n" + user_prompt
            user_prompt = user_prompt.replace("{sentence}", sentence)
        if dictref:
            if "{dict}" not in user_prompt:
                user_prompt += "\n\ndictionary reference:\n{dict}"
            user_prompt = user_prompt.replace("{dict}", dictref)
        else:
            user_prompt = user_prompt.replace("{dict}", "")
        return user_prompt

    def _query_ref_dict(self, word, sentence=None):
        if getattr(_ref_query_guard, "busy", False):
            return None
        if not self.config.get("use_ref_dict"):
            return None
        dict_id = self.config.get("ref_dict") or ""
        if not dict_id or dict_id == self.typename:
            return None
        cishu = getattr(getattr(gobject, "base", None), "cishus", {}).get(dict_id)
        if not cishu:
            return None
        _ref_query_guard.busy = True
        try:
            ret = []
            ev = threading.Event()

            def cb(result):
                ret.append(result)
                ev.set()

            cishu.safesearch(cb, word, sentence)
            if not ev.wait(30):
                return None
            res = ret[0] if ret else None
        except:
            print_exc()
            return None
        finally:
            _ref_query_guard.busy = False
        if not res or isinstance(res, Exception):
            return None
        text = html_to_plain_text(res)
        if not text:
            return None
        if len(text) > 8000:
            text = text[:8000] + "\n..."
        return text

    def search(self, word, sentence=None):
        dictref = self._query_ref_dict(word, sentence)
        extrabody, extraheader = getcustombodyheaders(
            self.config.get("customparams"), **locals()
        )
        query = self._gptlike_createquery(
            word, sentence, "use_user_user_prompt", "user_user_prompt_1", dictref
        )
        sysprompt = self._gptlike_createsys("使用自定义promt", "自定义promt")
        apitype = APIType(self.config["API接口地址"])
        if apitype == APIType.gemini:
            resp = self.query_gemini(apitype, sysprompt, query, extrabody, extraheader)
        elif apitype == APIType.claude:
            resp = self.query_cld(sysprompt, query, extrabody, extraheader)
        else:
            resp = self.search_1(apitype, sysprompt, query, extrabody, extraheader)
        think, resp = common_parse_normal_response(resp, apitype, splitthink=True)
        resp = NativeUtils.Markdown2Html(resp)
        if think:
            think = '<details style="border:2px solid"><summary style="text-align:center;background-color:pink;">Thinking</summary>{}</details>'.format(
                NativeUtils.Markdown2Html(think)
            )
            resp = think + resp
        return resp

    def query_cld(self, sysprompt, query, extrabody, extraheader):

        message = []
        message.append({"role": "user", "content": query})
        headers = {
            "anthropic-version": "2023-06-01",
            "accept": "application/json",
            "X-Api-Key": self.multiapikeycurrent["SECRET_KEY"],
        }
        data = dict(
            model=self.config["model"],
            messages=message,
            system=sysprompt,
            max_tokens=self.config["max_tokens"],
        )
        if self.config.get("Temperature.use", True):
            data.update(temperature=self.config["Temperature"])
        data.update(extrabody)
        headers.update(extraheader)
        response = self.proxysession.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=data,
        )
        return response

    def query_gemini(self, apitype, sysprompt, query, extrabody, extraheader):
        return common_create_gemini_request(
            self.proxysession,
            self.config,
            self.multiapikeycurrent["SECRET_KEY"],
            sysprompt,
            [{"role": "user", "parts": [{"text": query}]}],
            extraheader,
            extrabody,
            apitype,
        )
