# Copyright (c) Meta Platforms, Inc. and affiliates.
import copy
import json
from typing import Any

import requests
from playwright.sync_api import TimeoutError

from .base_environment_editor import BaseWebArenaEditor, WebArenaEditorException


class ShoppingEditor(BaseWebArenaEditor):
    def __init__(self, shopping_domain: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.environment = "shopping"
        self.shopping_domain = shopping_domain.rstrip("/")

    def login(self, username: str, password: str) -> bool:
        self.page.goto(
            f"{self.shopping_domain}/customer/account/login/",
            wait_until="networkidle",
        )

        try:
            self.page.wait_for_selector("#email", timeout=10000)
        except Exception:
            screenshot_path = "/tmp/debug_shoppingeditor_login_no_user_field.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Failed to login to shopping. Screenshot at {screenshot_path}"
            )

        self.page.fill("#email", username)
        self.page.fill("#pass", password)

        try:
            with self.page.expect_navigation():
                self.page.click('button.action.login.primary[type="submit"]')
        except TimeoutError:
            screenshot_path = "/tmp/debug_shoppingeditor_login.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Failed to login to shopping. Screenshot at {screenshot_path}"
            )

        if "/customer/account" not in self.page.url:
            screenshot_path = "/tmp/debug_shoppingeditor_login.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Failed to login to shopping. Ended up at {self.page.url}. Screenshot at {screenshot_path}"
            )

    def _get_admin_token(self) -> str:
        response = requests.post(
            url=f"{self.shopping_domain}/rest/default/V1/integration/admin/token",
            headers={"content-type": "application/json"},
            data=json.dumps({"username": "admin", "password": "admin1234"}),
            timeout=15,
        )
        response.raise_for_status()
        token = response.json()
        if not token:
            raise WebArenaEditorException("Failed to obtain shopping admin token.")
        return token

    def _get_admin_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_admin_token()}",
            "Content-Type": "application/json",
        }

    def _get_customer_token(self, username: str, password: str) -> str:
        response = requests.post(
            url=f"{self.shopping_domain}/rest/default/V1/integration/customer/token",
            headers={"content-type": "application/json"},
            data=json.dumps({"username": username, "password": password}),
            timeout=15,
        )
        response.raise_for_status()
        token = response.json()
        if not token or not isinstance(token, str):
            raise WebArenaEditorException("Failed to obtain shopping customer token.")
        return token

    def _get_customer_headers(self, username: str, password: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_customer_token(username, password)}",
            "Content-Type": "application/json",
        }

    def _get_product_response(self, sku: str) -> dict[str, Any]:
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/products/{sku}",
            headers=self._get_admin_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def get_product_info(self, sku: str) -> dict[str, str]:
        response_obj = self._get_product_response(sku)
        for custom_attribute in response_obj.get("custom_attributes", []):
            if custom_attribute.get("attribute_code") == "url_key":
                product_url = f"{self.shopping_domain}/{custom_attribute['value']}.html"
                return {
                    "name": response_obj["name"],
                    "url": product_url,
                    "review_url": f"{product_url}#reviews",
                }

        raise WebArenaEditorException(
            f"Could not resolve product page url for sku={sku}"
        )

    def get_product_page_url(self, sku: str) -> str:
        return self.get_product_info(sku)["url"]

    def get_customer_profile(self, customer_email: str) -> dict[str, Any]:
        params = {
            "searchCriteria[filter_groups][0][filters][0][field]": "email",
            "searchCriteria[filter_groups][0][filters][0][value]": customer_email,
            "searchCriteria[filter_groups][0][filters][0][condition_type]": "eq",
        }
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/customers/search",
            params=params,
            headers=self._get_admin_headers(),
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            raise WebArenaEditorException(
                f"Could not find shopping customer with email={customer_email}"
            )
        return items[0]

    @staticmethod
    def get_default_shipping_address(customer_profile: dict[str, Any]) -> dict[str, Any]:
        default_shipping_id = str(customer_profile.get("default_shipping", ""))
        for address in customer_profile.get("addresses", []):
            if address.get("default_shipping") or str(address.get("id", "")) == default_shipping_id:
                return address
        raise WebArenaEditorException("Could not find default shipping address.")

    def get_latest_order_info(self, customer_email: str) -> dict[str, Any]:
        return self.get_orders_for_customer(customer_email, page_size=1)[0]

    def get_orders_for_customer(
        self, customer_email: str, page_size: int = 10
    ) -> list[dict[str, Any]]:
        params = {
            "searchCriteria[filter_groups][0][filters][0][field]": "customer_email",
            "searchCriteria[filter_groups][0][filters][0][value]": customer_email,
            "searchCriteria[filter_groups][0][filters][0][condition_type]": "eq",
            "searchCriteria[sortOrders][0][field]": "created_at",
            "searchCriteria[sortOrders][0][direction]": "DESC",
            "searchCriteria[pageSize]": str(page_size),
        }
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/orders",
            params=params,
            headers=self._get_admin_headers(),
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("items", [])
        if not items:
            raise WebArenaEditorException(
                f"Could not find any orders for shopping customer email={customer_email}"
            )
        return items

    def get_order_view_url(self, increment_id: str) -> str:
        return f"{self.shopping_domain}/sales/order/view/order_id/{int(str(increment_id))}/"

    def get_page_text(self, url: str) -> str:
        self.page.goto(url, wait_until="networkidle")
        try:
            return (self.page.locator("body").text_content() or "").lower()
        except Exception:
            return self.page.content().lower()

    def get_product_reviews(self, sku: str) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/products/{sku}/reviews",
            headers=self._get_admin_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def delete_reviews_by_title(
        self, sku: str, review_title: str, nickname: str | None = None
    ) -> int:
        reviews = self.get_product_reviews(sku)
        num_deleted = 0
        for review in reviews:
            if review.get("title") != review_title:
                continue
            if nickname and review.get("nickname") != nickname:
                continue
            response = requests.delete(
                f"{self.shopping_domain}/rest/V1/reviews/{review['id']}",
                headers=self._get_admin_headers(),
                timeout=15,
            )
            response.raise_for_status()
            num_deleted += 1
        return num_deleted

    def get_cart_items(self, username: str, password: str) -> list[dict[str, Any]]:
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/carts/mine/items",
            headers=self._get_customer_headers(username, password),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def remove_cart_items_by_sku(
        self, username: str, password: str, sku: str
    ) -> int:
        num_deleted = 0
        while True:
            matching_items = [
                item
                for item in self.get_cart_items(username, password)
                if item.get("sku") == sku
            ]
            if not matching_items:
                break
            for item in matching_items:
                response = requests.delete(
                    f"{self.shopping_domain}/rest/V1/carts/mine/items/{item['item_id']}",
                    headers=self._get_customer_headers(username, password),
                    timeout=15,
                )
                response.raise_for_status()
                num_deleted += 1
        return num_deleted

    def delete_wishlist_items_by_product_name(self, product_name: str) -> int:
        self.page.goto(f"{self.shopping_domain}/wishlist/", wait_until="networkidle")
        try:
            wishlist_items = self.page.locator(
                ".products-grid.wishlist .product-item, #wishlist-view-form .product-item"
            )
            num_items = wishlist_items.count()
        except Exception:
            return 0

        num_deleted = 0
        for item_idx in range(num_items):
            wishlist_item = wishlist_items.nth(item_idx)
            try:
                item_text = (wishlist_item.inner_text() or "").lower()
            except Exception:
                continue
            if product_name.lower() not in item_text:
                continue

            self.page.once("dialog", lambda dialog: dialog.accept())
            delete_button = wishlist_item.locator(
                "a.action.delete, button.action.delete, a[title='Remove Item'], button[title='Remove Item']"
            ).first
            if delete_button.count() == 0:
                continue
            delete_button.click()
            self.page.wait_for_load_state("networkidle")
            num_deleted += 1
            self.page.goto(f"{self.shopping_domain}/wishlist/", wait_until="networkidle")
            return num_deleted + self.delete_wishlist_items_by_product_name(product_name)

        return num_deleted

    def get_customer_profile_by_id(self, customer_id: int | str) -> dict[str, Any]:
        response = requests.get(
            f"{self.shopping_domain}/rest/V1/customers/{customer_id}",
            headers=self._get_admin_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def update_customer_profile(self, customer_profile: dict[str, Any]) -> dict[str, Any]:
        customer_id = customer_profile["id"]
        response = requests.put(
            f"{self.shopping_domain}/rest/V1/customers/{customer_id}",
            headers=self._get_admin_headers(),
            data=json.dumps({"customer": customer_profile}),
            timeout=15,
        )
        response.raise_for_status()
        return response.json()

    def restore_customer_profile(
        self, customer_email: str, baseline_profile: dict[str, Any]
    ) -> dict[str, Any]:
        current_profile = self.get_customer_profile(customer_email)
        customer_profile = self.get_customer_profile_by_id(current_profile["id"])
        customer_profile["firstname"] = baseline_profile["firstname"]
        customer_profile["lastname"] = baseline_profile["lastname"]
        customer_profile["email"] = baseline_profile["email"]
        customer_profile["default_shipping"] = str(baseline_profile["default_shipping"])
        customer_profile["default_billing"] = str(baseline_profile["default_billing"])
        customer_profile["addresses"] = copy.deepcopy(baseline_profile["addresses"])
        return self.update_customer_profile(customer_profile)

    def cancel_orders_containing_sku_after_entity(
        self,
        customer_email: str,
        sku: str,
        baseline_order_entity_id: str | int,
    ) -> int:
        num_canceled = 0
        for order in self.get_orders_for_customer(customer_email, page_size=25):
            if int(order["entity_id"]) == int(baseline_order_entity_id):
                break
            order_skus = {item.get("sku") for item in order.get("items", [])}
            if sku not in order_skus:
                continue
            if order.get("state") in {"canceled", "closed", "complete"}:
                continue
            response = requests.post(
                f"{self.shopping_domain}/rest/V1/orders/{order['entity_id']}/cancel",
                headers=self._get_admin_headers(),
                timeout=15,
            )
            response.raise_for_status()
            if response.json() is True:
                num_canceled += 1
        return num_canceled

    def create_review_with_title_and_text(
        self,
        product_sku: str,
        review_title: str,
        review_text: str,
        nickname: str = "Arsene Lupin",
        rating: int = 5,
    ) -> str:
        rating = max(1, min(5, int(rating)))
        product_url = self.get_product_page_url(product_sku)
        product_review_url = f"{product_url}#reviews"

        self.page.goto(product_review_url, wait_until="networkidle")

        try:
            self.page.wait_for_selector("#tab-label-reviews-title", timeout=10000)
            self.page.click("#tab-label-reviews-title")
            self.page.wait_for_selector("#review-form", timeout=10000)
        except Exception:
            screenshot_path = "/tmp/debug_shoppingeditor_review_form.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Shopping review form not found for sku={product_sku}. Screenshot at {screenshot_path}"
            )

        try:
            self.page.click(f"label#Rating_{rating}_label", force=True)
        except Exception:
            self.page.locator(f"label#Rating_{rating}_label").evaluate(
                "(node) => node.click()"
            )
        self.page.fill("#nickname_field", nickname)
        self.page.fill("#summary_field", review_title)
        self.page.fill("#review_field", review_text)

        try:
            self.page.click("button.action.submit.primary")
            self.page.wait_for_load_state("networkidle")
        except TimeoutError:
            screenshot_path = "/tmp/debug_shoppingeditor_review_submit.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Failed to submit shopping review for sku={product_sku}. Screenshot at {screenshot_path}"
            )

        success_msg = "You submitted your review for moderation."
        if success_msg not in self.page.content():
            screenshot_path = "/tmp/debug_shoppingeditor_review_submit.png"
            self.page.screenshot(path=screenshot_path)
            raise WebArenaEditorException(
                f"Review submission may have failed for sku={product_sku}. Screenshot at {screenshot_path}"
            )

        return product_review_url
