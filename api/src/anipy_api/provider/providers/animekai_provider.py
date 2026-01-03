import json
import re
from typing import TYPE_CHECKING, List
from urllib.parse import urljoin
from requests import Session
from requests import HTTPError

from anipy_api.provider.base import ExternalSub
import m3u8
from bs4 import BeautifulSoup
from requests import Request

from anipy_api.error import BeautifulSoupLocationError, LangTypeNotAvailableError
from anipy_api.provider import (
    BaseProvider,
    LanguageTypeEnum,
    ProviderInfoResult,
    ProviderSearchResult,
    ProviderStream,
)
from anipy_api.provider.filter import (
    BaseFilter,
    FilterCapabilities,
    Filters,
    MediaType,
    Season,
    Status,
)
from anipy_api.provider.utils import (
    get_language_code2,
    parsenum,
    request_page,
    safe_attr,
)

if TYPE_CHECKING:
    from requests import Session

    from anipy_api.provider import Episode

DEC_API = "https://enc-dec.app/api"

def generate_token(text):
    req = Request("GET", f"{DEC_API}/enc-kai?text={text}")
    res = request_page(Session(), req)
    return res.json()["result"]

def decode_iframe_data(text):
    req = Request("GET", f"{DEC_API}/dec-kai?text={text}")
    res = request_page(Session(), req)
    return res.json()["result"]

def decode_mega(text):
    req = Request("POST", f"{DEC_API}/dec-mega", json={
        "text": text, 
        "agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    })
    res = request_page(Session(), req)
    return res.json()["result"]

class AnimekaiFilter(BaseFilter):
    def _apply_query(self, query: str):
        self._request.params.update({"keyword": query})

    def _apply_year(self, year: int):
        self._request.params.update({"year[]": [year]})

    def _apply_season(self, season: Season):
        mapping = {v: k.lower() for k, v in Season._member_map_.items()}
        self._request.params.update({"season[]": [mapping[season]]})

    def _apply_status(self, status: Status):
        mapping = {
            Status.UPCOMING: "info",
            Status.ONGOING: "releasing",
            Status.COMPLETED: "completed",
        }
        self._request.params.update({"status[]": [mapping[status]]})

    def _apply_media_type(self, media_type: MediaType):
        mapping = {
            MediaType.MOVIE: [1],
            MediaType.TV: [2],
            MediaType.OVA: [3],
            MediaType.SPECIAL: [4, 7],
            MediaType.ONA: [5],
            MediaType.MUSIC: [6],
        }
        self._request.params.update({"type[]": mapping[media_type]})


class AnimekaiProvider(BaseProvider):
    """For detailed documentation have a look
    at the [base class][anipy_api.provider.base.BaseProvider].

    Attributes:
        NAME: animekai
        BASE_URL: https://animekai.to
        FILTER_CAPS: YEAR, SEASON, STATUS, MEDIA_TYPE, NO_QUERY
    """

    NAME: str = "animekai"
    BASE_URL: str = "https://anikai.to"
    FILTER_CAPS: FilterCapabilities = (
        FilterCapabilities.YEAR
        | FilterCapabilities.SEASON
        | FilterCapabilities.STATUS
        | FilterCapabilities.MEDIA_TYPE
        | FilterCapabilities.NO_QUERY
    )

    def get_search(
        self, query: str, filters: "Filters" = Filters()
    ) -> List[ProviderSearchResult]:
        search_url = self.BASE_URL + "/browser"
        req = Request("GET", search_url)
        req = AnimekaiFilter(req).apply(query, filters)

        results = []
        has_next = True
        page = 0
        while has_next:
            req.params["page"] = page + 1
            res = self._request_page(req)
            soup = BeautifulSoup(res.text, "html.parser")
            has_next = bool(soup.find("li", attrs={"class": "page-item next"}))
            anime = soup.find_all("div", class_="aitem")
            for a in anime:
                uri = a.div.a["href"]
                identifier = uri.split("/")[-1]
                if identifier is None:
                    continue

                name = a.find("a", class_="title")["title"]
                languages = {LanguageTypeEnum.SUB}
                has_dub = a.find("span", class_="dub")
                if has_dub is not None:
                    languages.add(LanguageTypeEnum.DUB)

                results.append(
                    ProviderSearchResult(
                        identifier=identifier, name=name, languages=languages
                    )
                )

            page += 1
        return results

    def get_episodes(self, identifier: str, lang: LanguageTypeEnum) -> List["Episode"]:
        req = Request("GET", f"{self.BASE_URL}/watch/{identifier}")
        result = self._request_page(req)
        soup = BeautifulSoup(result.text, "html.parser")
        ani_id = safe_attr(soup.find("div", class_="rate-box"), "data-id")
        req = Request(
            "GET",
            f"{self.BASE_URL}/ajax/episodes/list",
            params={"ani_id": ani_id, "_": generate_token(ani_id)},
        )
        json_res = json.loads(self._request_page(req).text)
        soup = BeautifulSoup(json_res["result"], "html.parser")
        ep_elements = soup.find_all("a", attrs={"num": re.compile(r"\d")})
        ep_list = []
        map = {"1": ["sub"], "3": ["sub", "dub"]}
        for e in ep_elements:
            lang_num = safe_attr(e, "langs")
            episode_num = safe_attr(e, "num")
            if episode_num is None or lang_num is None:
                raise BeautifulSoupLocationError("episode", result.url)
            if lang.value in map[lang_num]:
                ep_list.append(parsenum(episode_num))

        return ep_list

    def get_info(self, identifier: str) -> "ProviderInfoResult":
        req = Request("GET", f"{self.BASE_URL}/watch/{identifier}")
        result = self._request_page(req)

        data_map = {
            "name": None,
            "image": None,
            "genres": [],
            "synopsis": None,
            "release_year": None,
            "status": None,
            "alternative_names": [],
        }

        soup = BeautifulSoup(result.text, "html.parser")
        data_map["name"] = safe_attr(soup.find("div", class_="title"), "text")
        data_map["synopsis"] = safe_attr(
            soup.find("div", class_="desc text-expand"), "text"
        )
        data_map["image"] = safe_attr(soup.select_one(".poster img"), "src")
        soup.find("div", class_="detail")

        alt_names = safe_attr(soup.find("small", attrs={"class": "al-title"}), "text")
        data_map["alternative_names"] = alt_names.split(";") if alt_names else []

        data = soup.find("div", class_="detail")
        if data is None:
            return ProviderInfoResult(**data_map)

        for i in data.find_all("div"):  # type: ignore
            text = i.contents[0].strip()
            if text == "Genres:":
                data_map["genres"] = [
                    safe_attr(j, "text")
                    for j in i.find_all("a", attrs={"href": re.compile(r"genres\/.+")})
                ]
            elif text == "Status:":
                desc = safe_attr(i.find("span"), "text")
                if desc is not None:
                    map = {
                        "Info": "UPCOMING",
                        "Releasing": "ONGOING",
                        "Completed": "COMPLETED",
                    }
                    data_map["status"] = Status[map[desc]]
            elif text == "Premiered:":
                desc = safe_attr(i.find("a"), "text")
                if desc is not None:
                    try:
                        data_map["release_year"] = int(desc.split()[-1])
                    except (ValueError, TypeError):
                        pass
            else:
                continue

        return ProviderInfoResult(**data_map)

    def get_video(
        self, identifier: str, episode: "Episode", lang: LanguageTypeEnum
    ) -> List["ProviderStream"]:

        req = Request("GET", f"{self.BASE_URL}/watch/{identifier}")
        result = self._request_page(req)
        soup = BeautifulSoup(result.text, "html.parser")
        ani_id = safe_attr(soup.find("div", class_="rate-box"), "data-id")
        req = Request(
            "GET",
            f"{self.BASE_URL}/ajax/episodes/list",
            params={"ani_id": ani_id, "_": generate_token(ani_id)},
        )
        res = self._request_page(req)
        json_res = json.loads(res.text)
        soup = BeautifulSoup(json_res["result"], "html.parser")
        token = safe_attr(soup.find("a", attrs={"num": episode}), "token")

        req = Request(
            "GET",
            f"{self.BASE_URL}/ajax/links/list",
            params={"token": token, "_": generate_token(token)},
        )
        res = self._request_page(req)
        json_res = json.loads(res.text)
        soup = BeautifulSoup(json_res["result"], "html.parser")
        div_tag = soup.find_all(
            "div",
            class_="server-items lang-group",
            attrs={"data-id": re.compile(rf"^(soft{lang}|{lang})$")},
        )
        if div_tag is None:
            raise LangTypeNotAvailableError(identifier, self.NAME, lang)

        substreams = []
        video_url = []
        corrections = {
            "English Espaأ±ol": "Spanish",
            "English Portuguأھs (Brasil)": "Portuguese",
        }
        for i in div_tag:
            servers = i.find_all("span", class_="server")
            for server in servers:
                video_entry = []
                video_subtitles = {}
                id = safe_attr(server, "data-lid")
                req = Request(
                    "GET",
                    f"{self.BASE_URL}/ajax/links/view",
                    params={"id": id, "_": generate_token(id)},
                )
                res = self._request_page(req)
                json_res = json.loads(res.text)
                json_res = decode_iframe_data(json_res["result"])
                mega_url = json_res["url"].replace("/e/", "/media/")
                req = Request("GET", mega_url)
                res = self._request_page(req)
                json_res = json.loads(res.text)
                json_res = decode_mega(json_res["result"])
                video_entry.append(json_res["sources"][0]["file"])
                for track in json_res.get("tracks", []):
                    if track.get("kind") == "captions":
                        lang_name = corrections.get(
                            track.get("label"), track.get("label")
                        ).split()[0]
                        lang_code = get_language_code2(lang_name)
                        video_subtitles[track.get("label")] = ExternalSub(
                            url=track["file"],
                            lang=lang_name,
                            shortcode=lang_code,
                            codec="vtt",
                        )
                video_entry.append(video_subtitles)
                video_url.append(video_entry)

        for video in video_url:
            req = Request("GET", video[0])
            try:
                res = self._request_page(req)
            except HTTPError:
                continue
            content = m3u8.M3U8(res.text, base_uri=urljoin(res.url, "."))
            if len(content.playlists) == 0:
                substreams.append(
                    ProviderStream(
                        url=video[0],
                        resolution=1080,
                        episode=episode,
                        language=lang,
                        subtitle=video[1],
                        referrer=self.BASE_URL,
                    )
                )

            for sub_playlist in content.playlists:
                substreams.append(
                    ProviderStream(
                        url=urljoin(content.base_uri, sub_playlist.uri),
                        resolution=sub_playlist.stream_info.resolution[1],
                        episode=episode,
                        language=lang,
                        subtitle=video[1],
                        referrer=self.BASE_URL,
                    )
                )

        return substreams
