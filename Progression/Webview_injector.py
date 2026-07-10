# This script is part of the Anki Progression add-on.
# Source: github.com/Eltaurus-Lt/Lt-Anki-Addons
# 
# Copyright © 2026 Eltaurus
# Contact: 
#     Email: Eltaurus@inbox.lt
#     GitHub: github.com/Eltaurus-Lt
#     Anki Forums: forums.ankiweb.net/u/Eltaurus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

from aqt import gui_hooks, mw
from aqt.deckbrowser import DeckBrowser
from aqt.overview import Overview
from aqt.editor import Editor
from aqt.reviewer import Reviewer
from aqt.browser.previewer import Previewer
from aqt.clayout import CardLayout
import os


mw.addonManager.setWebExports(__name__, r"css/.*\.css|js/.*\.js|user_files/(.*\.(css|js))$")
addons_folder = mw.addonManager.addonsFolder()
addon_name = mw.addonManager.addonFromModule(__name__)

def injectable_type(inj_filename):
    return os.path.splitext(inj_filename)[1].lstrip(".").lower()

def general_injector(inj_filename, web_content):
    inj_type = injectable_type(inj_filename)

    if inj_type == "css":
        target = web_content.css
    elif inj_type == "js":
        target = web_content.js
    else:
        return

    if os.path.exists(os.path.join(addons_folder, addon_name, inj_type, inj_filename)):
        target.append(f"/_addons/{addon_name}/{inj_type}/{inj_filename}")
    if os.path.exists(os.path.join(addons_folder, addon_name, "user_files", inj_filename)):
        target.append(f"/_addons/{addon_name}/user_files/{inj_filename}")
        

def window_injector(web_content, context: None):

    def inject(inj_filename):
        return general_injector(inj_filename, web_content)

    inject("common_styles.css")
    inject("common_scripts.js")

    if isinstance(context, DeckBrowser):
        inject("home_styles.css")
        inject("home_scripts.js")

    if isinstance(context, Overview):
        inject("deck_styles.css")
        inject("deck_scripts.js")        

    if isinstance(context, Editor):
        inject("editor_styles.css")
        inject("editor_scripts.js")

    if isinstance(context, (Reviewer, Previewer, CardLayout)):
        inject("reviewer_styles.css")
        inject("reviewer_scripts.js")

def congrats_injector(webview):
    if "congrats" not in webview.page().url().toString(): return

    def inject(inj_filename):
        inj_type = injectable_type(inj_filename)

        def inject_url(inj_url):
            if inj_type == "css":
                webview.eval(f"""
                    (()=>{{
                        const cssL = document.createElement('link');
                        cssL.rel = 'stylesheet';
                        cssL.type = 'text/css';
                        cssL.href = `{inj_url}`;
                        document.head.appendChild(cssL);
                    }})();
                """)
            elif inj_type == "js":
                webview.eval(f"""
                    (()=>{{
                        const jsL = document.createElement('script');
                        jsL.setAttribute(`src`, `{inj_url}`);
                        document.head.appendChild(jsL);
                    }})();                    
                """)

        if os.path.exists(os.path.join(addons_folder, addon_name, inj_type, inj_filename)):
            inject_url(f"/_addons/{addon_name}/{inj_type}/{inj_filename}")
        if os.path.exists(os.path.join(addons_folder, addon_name, "user_files", inj_filename)):
            inject_url(f"/_addons/{addon_name}/user_files/{inj_filename}")

    inject("common_styles.css")
    inject("common_scripts.js")
    inject("deck_styles.css")
    inject("deck_scripts.js") 

gui_hooks.webview_will_set_content.append(window_injector)
gui_hooks.webview_did_inject_style_into_page.append(congrats_injector) # occasionally triggered twice when displaying congrats

