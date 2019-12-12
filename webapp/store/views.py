from math import ceil, floor
from urllib.parse import quote_plus
import talisker.requests
import flask
import webapp.helpers as helpers
import webapp.store.logic as logic
from webapp.api import requests
from canonicalwebteam.store_api.stores.snapcraft import SnapcraftStoreApi
from canonicalwebteam.store_api.exceptions import (
    StoreApiCircuitBreaker,
    StoreApiConnectionError,
    StoreApiError,
    StoreApiResponseDecodeError,
    StoreApiResponseError,
    StoreApiResponseErrorList,
    StoreApiTimeoutError,
)
from webapp.snapcraft import logic as snapcraft_logic
from webapp.store.snap_details_views import snap_details_views


def store_blueprint(store_query=None, testing=False):
    if testing:
        session = talisker.requests.get_session(requests.Session)
    else:
        session = talisker.requests.get_session(requests.CachedSession)

    api = SnapcraftStoreApi(session, store_query)

    store = flask.Blueprint(
        "store",
        __name__,
        template_folder="/templates",
        static_folder="/static",
    )

    def _handle_errors(api_error: StoreApiError):
        status_code = 502
        error = {"message": str(api_error)}

        if type(api_error) is StoreApiTimeoutError:
            status_code = 504
        elif type(api_error) is StoreApiResponseDecodeError:
            status_code = 502
        elif type(api_error) is StoreApiResponseErrorList:
            error["errors"] = api_error.errors
            status_code = 502
        elif type(api_error) is StoreApiResponseError:
            status_code = 502
        elif type(api_error) is StoreApiConnectionError:
            status_code = 502
        elif type(api_error) is StoreApiCircuitBreaker:
            # Special case for this one, because it is the only case where we
            # don't want the user to be able to access the page.
            return flask.abort(503)

        return status_code, error

    snap_details_views(store, api, _handle_errors)

    @store.route("/discover")
    def discover():
        return flask.redirect(flask.url_for(".homepage"))

    def store_view():
        error_info = {}
        status_code = 200

        try:
            categories_results = api.get_categories()
        except StoreApiError:
            categories_results = []

        categories = logic.get_categories(categories_results)

        try:
            featured_snaps_results = api.get_searcheds(
                search="", category="featured", size=10, page=1
            )
        except StoreApiError as api_error:
            status_code, error_info = _handle_errors(api_error)
            return flask.abort(status_code)

        featured_snaps = logic.get_searched_snaps(featured_snaps_results)

        if not featured_snaps:
            return flask.abort(503)

        # if the first snap (banner snap) doesn't have an icon, remove the last
        # snap from the list to avoid a hanging snap (grid of 9)
        if len(featured_snaps) == 10 and featured_snaps[0]["icon_url"] == "":
            featured_snaps = featured_snaps[:-1]

        for index in range(len(featured_snaps)):
            featured_snaps[index] = logic.get_snap_banner_url(
                featured_snaps[index]
            )

        livestream = snapcraft_logic.get_livestreams()

        return (
            flask.render_template(
                "store/store.html",
                categories=categories,
                has_featured=True,
                featured_snaps=featured_snaps,
                error_info=error_info,
                livestream=livestream,
            ),
            status_code,
        )

    def brand_store_view():
        error_info = {}
        status_code = 200

        try:
            snaps_results = api.get_all(size=16, api_version=2)
        except StoreApiError as api_error:
            snaps_results = []
            status_code, error_info = _handle_errors(api_error)

        snaps = logic.get_searched_snaps(snaps_results)

        return (
            flask.render_template(
                "brand-store/store.html", snaps=snaps, error_info=error_info
            ),
            status_code,
        )

    def search_snap():
        status_code = 200
        snap_searched = flask.request.args.get("q", default="", type=str)
        snap_category = flask.request.args.get(
            "category", default="", type=str
        )
        page = flask.request.args.get("page", default=1, type=int)

        if snap_category:
            snap_category_display = snap_category.capitalize().replace(
                "-", " "
            )
        else:
            snap_category_display = None

        if not snap_searched and not snap_category:
            return flask.redirect(flask.url_for(".homepage"))

        # The default size should be 44 (rows of 4)
        # it's important that this is smaller than the category page 1 size
        # below otherwise snaps can be missed out of results
        size = 44

        # Page 1 has a snap at the top, and a few rows of 3, followed by rows
        # of 4 - so we need to offset to ensure there's no hanging snap
        if snap_category and page == 1:
            size = 47

        error_info = {}
        searched_results = []

        try:
            searched_results = api.get_searcheds(
                quote_plus(snap_searched),
                category=snap_category,
                size=size,
                page=page,
            )
        except StoreApiError as api_error:
            status_code, error_info = _handle_errors(api_error)

        total_pages = None

        if "total" in searched_results:
            total_results_count = searched_results["total"]
            total_pages = ceil(total_results_count / size)
        else:
            total_results_count = None

        snaps_results = logic.get_searched_snaps(searched_results)

        links = {}

        if page > 1:
            links["first"] = logic.build_pagination_link(
                snap_searched=snap_searched,
                snap_category=snap_category,
                page=1,
            )
            links["prev"] = logic.build_pagination_link(
                snap_searched=snap_searched,
                snap_category=snap_category,
                page=page - 1,
            )

        if not total_pages or page < total_pages:
            links["next"] = logic.build_pagination_link(
                snap_searched=snap_searched,
                snap_category=snap_category,
                page=page + 1,
            )
            if total_pages:
                links["last"] = logic.build_pagination_link(
                    snap_searched=snap_searched,
                    snap_category=snap_category,
                    page=total_pages,
                )

        featured_snaps = []

        # These are the hand-selected "featured snaps" in each category.
        # We don't have this information on the API, so it's hardcoded.
        number_of_featured_snaps = 19

        if snap_category_display and page == 1:
            if snaps_results and snaps_results[0]:
                if snaps_results[0]["icon_url"] == "":
                    snaps_results = logic.promote_snap_with_icon(snaps_results)

                snaps_results[0] = logic.get_snap_banner_url(snaps_results[0])

                if (
                    snap_category == "featured"
                    or len(snaps_results) < number_of_featured_snaps
                ):
                    featured_snaps = snaps_results
                    snaps_results = []
                else:
                    featured_snaps = snaps_results[:number_of_featured_snaps]
                    snaps_results = snaps_results[number_of_featured_snaps:]

        context = {
            "query": snap_searched,
            "category": snap_category,
            "category_display": snap_category_display,
            "searched_snaps": snaps_results,
            "featured_snaps": featured_snaps,
            "total": total_results_count,
            "links": links,
            "page": page,
            "error_info": error_info,
        }

        return (
            flask.render_template("store/search.html", **context),
            status_code,
        )

    def brand_search_snap():
        status_code = 200
        snap_searched = flask.request.args.get("q", default="", type=str)

        if not snap_searched:
            return flask.redirect(flask.url_for(".homepage"))

        size = flask.request.args.get("limit", default=25, type=int)
        offset = flask.request.args.get("offset", default=0, type=int)

        try:
            page = floor(offset / size) + 1
        except ZeroDivisionError:
            size = 10
            page = floor(offset / size) + 1

        error_info = {}
        searched_results = []

        try:
            searched_results = api.get_searcheds(
                quote_plus(snap_searched), size=size, page=page
            )
        except StoreApiError as api_error:
            status_code, error_info = _handle_errors(api_error)

        snaps_results = logic.get_searched_snaps(searched_results)
        links = logic.get_pages_details(
            flask.request.base_url,
            (
                searched_results["_links"]
                if "_links" in searched_results
                else []
            ),
        )

        context = {
            "query": snap_searched,
            "snaps": snaps_results,
            "links": links,
            "error_info": error_info,
        }

        return (
            flask.render_template("brand-store/search.html", **context),
            status_code,
        )

    @store.route("/publisher/<regex('[a-z0-9-]*[a-z][a-z0-9-]*'):publisher>")
    def publisher_details(publisher):
        """
        A view to display the publisher details page for specific publisher.
        """

        publisher_content_path = flask.current_app.config["CONTENT_DIRECTORY"][
            "PUBLISHER_PAGES"
        ]

        context = helpers.get_yaml(
            publisher_content_path + publisher + ".yaml", typ="safe"
        )

        if not context:
            flask.abort(404)

        if "publishers" in context:
            context["snaps"] = []
            for publisher in context["publishers"]:
                searched_results = []
                try:
                    searched_results = api.get_searcheds(
                        "publisher:" + publisher, size=500, page=1
                    )
                except StoreApiError:
                    pass

                snaps_results = logic.get_searched_snaps(searched_results)
                context["snaps"].extend(
                    [snap for snap in snaps_results if snap["apps"]]
                )

        if "snaps" not in context:
            snaps = helpers.get_yaml(
                publisher_content_path + publisher + "-snaps.yaml", typ="safe"
            )

            context["snaps"] = snaps["snaps"]

        featured_snaps = [
            snap["package_name"] for snap in context["featured_snaps"]
        ]

        context["snaps"] = [
            snap
            for snap in context["snaps"]
            if snap["package_name"] not in featured_snaps
        ]

        return flask.render_template("store/publisher-details.html", **context)

    @store.route("/store/categories/<category>")
    def store_category(category):
        status_code = 200
        error_info = {}
        category_results = []

        try:
            category_results = api.get_searcheds(
                search="", category=category, size=10, page=1
            )
        except StoreApiError as api_error:
            status_code, error_info = _handle_errors(api_error)

        snaps_results = logic.get_searched_snaps(category_results)

        # if the first snap (banner snap) doesn't have an icon, remove the last
        # snap from the list to avoid a hanging snap (grid of 9)
        if len(snaps_results) == 10 and snaps_results[0]["icon_url"] == "":
            snaps_results = snaps_results[:-1]

        for index in range(len(snaps_results)):
            snaps_results[index] = logic.get_snap_banner_url(
                snaps_results[index]
            )

        context = {
            "category": category,
            "has_featured": True,
            "snaps": snaps_results,
            "error_info": error_info,
        }

        return (
            flask.render_template("store/_category-partial.html", **context),
            status_code,
        )

    if store_query:
        store.add_url_rule("/", "homepage", brand_store_view)
        store.add_url_rule("/search", "search", brand_search_snap)
    else:
        store.add_url_rule("/store", "homepage", store_view)
        store.add_url_rule("/search", "search", search_snap)

    return store
