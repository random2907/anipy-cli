import functools
import base64
import json
import re
from typing import TYPE_CHECKING, List
from urllib.parse import urljoin
from requests import Session
from requests import HTTPError

from anipy_api.provider.base import ExternalSub
import m3u8
from bs4 import BeautifulSoup, Tag
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
from anipy_api.provider.utils import get_language_code2, parsenum, request_page, safe_attr

if TYPE_CHECKING:
    from requests import Session

    from anipy_api.provider import Episode

class AnikotoFilter(BaseFilter):
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


class AnikotoProvider(BaseProvider):
    """For detailed documentation have a look
    at the [base class][anipy_api.provider.base.BaseProvider].

    Attributes:
        NAME: anikoto
        BASE_URL: https://anikoto.to
        FILTER_CAPS: YEAR, SEASON, STATUS, MEDIA_TYPE, NO_QUERY
    """

    NAME: str = "anikoto"
    BASE_URL: str = "https://anikoto.to"
    FILTER_CAPS: FilterCapabilities = (
        FilterCapabilities.YEAR
        | FilterCapabilities.SEASON
        | FilterCapabilities.STATUS
        | FilterCapabilities.MEDIA_TYPE
        | FilterCapabilities.NO_QUERY
    )

    headers = { 'X-Requested-With': 'XMLHttpRequest' }

    def get_search(
        self, query: str, filters: "Filters" = Filters()
    ) -> List[ProviderSearchResult]:
        search_url = self.BASE_URL + "/filter"
        req = Request("GET", search_url, headers=self.headers)
        req = AnikotoFilter(req).apply(query, filters)

        results = []
        has_next = True
        page = 1
        while has_next and page<= 10 :
            req.params["page"] = page
            res = self._request_page(req)
            soup = BeautifulSoup(res.text, "html.parser")
            has_next = bool(soup.find("a", attrs={"class": "page-link", "rel" : "next"}))
            anime_list = soup.find_all("div", class_="item")
            for anime in anime_list:
                if isinstance(anime, Tag):
                    identifier = safe_attr(anime.find('div', class_='ani poster tip'), "data-tip")
                    name = safe_attr(anime.div.img, "alt")
                    languages = {LanguageTypeEnum.SUB}
                    has_dub = bool(anime.a.find("span", class_="ep-status dub"))
                    if has_dub:
                        languages.add(LanguageTypeEnum.DUB)

                    results.append(
                        ProviderSearchResult(
                            identifier=identifier, name=name, languages=languages
                        )
                    )

            page += 1
        return results

    def get_episodes(self, identifier: str, lang: LanguageTypeEnum) -> List["Episode"]:
        req = Request("GET", f"{self.BASE_URL}/ajax/episode/list/{identifier}", headers=self.headers)
        result = self._request_page(req).json()
        soup = BeautifulSoup(result["result"], "html.parser")
        ep_list = []
        for episode in soup.find_all("a"):
            if isinstance( episode, Tag):
                if safe_attr(episode, f"data-{lang}") == "1":
                    episode_num = safe_attr(episode, "data-num")
                    if episode_num:
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
        
        alt_names = safe_attr(
            soup.find("small", attrs={"class": "al-title"}), "text"
        )
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

        req = Request("GET", f"{self.BASE_URL}/ajax/episode/list/{identifier}", headers=self.headers)
        result = self._request_page(req).json()
        soup = BeautifulSoup(result["result"], "html.parser")
        episode_id = safe_attr(soup.find_all("a")[episode-1], "data-ids")

        kiwi_slug = safe_attr(soup.find_all("a")[episode-1], "data-slug")
        kiwi_mal = safe_attr(soup.find_all("a")[episode-1], "data-mal")
        kiwi_timestamp = safe_attr(soup.find_all("a")[episode-1], "data-timestamp")

        req = Request("GET", f"{self.BASE_URL}/ajax/server/list?servers={episode_id}", headers=self.headers)
        result = self._request_page(req).json()
        soup = BeautifulSoup(result["result"], "html.parser")
        megaplay_servers = soup.find_all("div", class_="type")
        for i in megaplay_servers:
            if safe_attr(i, "data-type") == lang.value:
                server = safe_attr(i.find("li"), "data-link-id")

        req = Request("GET", f"{self.BASE_URL}/ajax/server?get={server}", headers=self.headers)
        result = self._request_page(req).json()
        video_url = result["result"]["url"]

        req = Request(
            "GET",
            video_url,
            headers={"Referer": self.BASE_URL}
        )
        result = self._request_page(req)
        soup = BeautifulSoup(result.text, "html.parser")
        megaplay_id= safe_attr(soup.find("div", class_="fix-area"), "data-id")

        req = Request(
            "GET",
            f"https://megaplay.buzz/stream/getSources?id={megaplay_id}&id={megaplay_id}",
            headers={"Referer": self.BASE_URL}
        )
        
        result = self._request_page(req).json()

        substreams = []
        subs = {}
        megaplay_video = result["sources"]["file"]
        for sub in result["tracks"]:
                subs[sub["label"]] = ExternalSub(
                        url=sub["file"],
                        shortcode=get_language_code2(sub["label"].split("-")[0].strip()),
                        codec="vtt",
                        lang=sub["label"].split("-")[0].strip()
                    )

        kiwi_api = "https://mapper.kotostream.online/api/mal/"
        req = Request(
            "GET",
            f"{kiwi_api}{kiwi_mal}/{kiwi_slug}/{kiwi_timestamp}",
        )

        result = self._request_page(req).json()

        for qual, url_dict in result.items():
            kiwi_id = url_dict.get(lang.value, {}).get("url")
            if kiwi_id:
                req = Request("GET", f"{self.BASE_URL}/ajax/server?get={kiwi_id}", headers=self.headers)
                res = self._request_page(req).json()
                url=base64.b64decode(res["result"]["url"].split("#")[1]).decode()
                substreams.append(
                    ProviderStream(
                        url=url,
                        resolution=int(qual.split("-")[-1].replace("p","")),
                        episode=episode,
                        language=lang,
                        subtitle=None,
                        referrer=None,
                    )
                )
                
        referer = "https://megaplay.buzz/"
        req = Request("GET", megaplay_video, headers={"Referer": referer})
        result = self._request_page(req)
        content = m3u8.M3U8(result.text, base_uri=urljoin(megaplay_video,"."))
        if len(content.playlists) == 0:
            substreams.append(
                ProviderStream(
                    url=megaplay_video,
                    resolution=1080,
                    episode=episode,
                    language=lang,
                    subtitle=subs,
                    referrer=referer,
                )
            )
        for sub_playlist in content.playlists:
            substreams.append(
                ProviderStream(
                    url=urljoin(content.base_uri, sub_playlist.uri),
                    resolution=sub_playlist.stream_info.resolution[1],
                    episode=episode,
                    language=lang,
                    subtitle=subs,
                    referrer=referer,
                )
            )

        return substreams

