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

import time
from aqt import mw, gui_hooks
from aqt.reviewer import Reviewer
from aqt.deckbrowser import DeckBrowser
from aqt.overview import Overview

from . import Webview_injector
from aqt.utils import tooltip

# todo: 
#   -next
#   iterate over decks for home screen
#   anki colors
#
# learning siblings
# filtered decks
# manually scheduled cards
# non-monotonic learning steps?

def progress_bar_template():
    return """
        <div id="lt-progress-bar">
            <div class="lt-progress-segment new done"></div>
            <div class="lt-progress-segment new todo"></div>
            <span></span>
            <div class="lt-progress-segment learn done"></div>
            <div class="lt-progress-segment learn done hold"></div>
            <div class="lt-progress-segment learn todo"></div>
            <div class="lt-progress-segment learn future"></div>
            <span></span>
            <div class="lt-progress-segment review done"></div>
            <div class="lt-progress-segment review todo"></div>
            <span></span>
            <div class="lt-progress-segment re learn done"></div>
            <div class="lt-progress-segment re learn done hold"></div>
            <div class="lt-progress-segment re learn todo"></div>
            <div class="lt-progress-segment re learn future"></div>
        </div>
    """

def steps_setting(did, card_state: int):
    try:
        config = mw.col.decks.config_dict_for_deck_id(did)
        if card_state == 3: # relearn
            return config.get("lapse", {}).get("delays", [10])
        else: # learn
            return config.get("new", {}).get("delays", [1, 10])
    except:
        return [10] if card_state == 3 else [1, 10] # anki defaults (10m / 1m 10m)

def short_term_count(steps):
    return len([step for step in steps if step < 1440]) # count steps < 1d

def deck_new_count(did):
    deck = mw.col.sched.deck_due_tree(did)
    if not deck:
        return (0, 0)
    new_ni = max(0, deck.new_count)        
    new_li = 0
    
    if new_ni > 0:
        subdids_str = ",".join(map(str, mw.col.decks.deck_and_child_ids(did)))
        
        decks_new_counts = mw.col.db.all(
            f"SELECT did, count() FROM cards WHERE queue = 0 AND did IN ({subdids_str}) GROUP BY did"
        )
        
        total_new_count = sum(new_count for _, new_count in decks_new_counts)
        
        if total_new_count > 0:
            for sub_did, new_count in decks_new_counts:
                new_limited = new_count * new_ni / total_new_count # only an estimate -- the exact decks (=> learning steps) for new card being pulled are not predetermined
                new_li += new_limited * short_term_count(steps_setting(sub_did, 1))

    # (NEW cards, future LEARN cards)
    return  (new_ni, new_li) 

def upd_progress(*args, **kwargs):
    if not mw.col or not mw.col.sched: return
    
    is_db = (mw.state == "deckBrowser")
    current_did = 0 if is_db else mw.col.decks.selected()
    tree = mw.col.sched.deck_due_tree(current_did)
    dids = [d["id"] for d in mw.col.decks.all()] if is_db else mw.col.decks.deck_and_child_ids(current_did)
    
    cutoff = mw.col.sched.day_cutoff
    current_time = int(time.time())
    current_day = mw.col.sched.today

    # REVIEW cards
    rev_n = max(0, tree.review_count)
    if not is_db:
        try: 
            rev_n = max(0, mw.col.sched.get_queued_cards().review_count) # returns count for the current deck
        except: 
            pass
    
    # (RE)LEARN cards | queue = 1 - intraday, 3 - interday | type = 1 - learning, 3 - relearning
    db_data = mw.col.db.all(
            f"""
            SELECT did, due, left, type
            FROM cards
            WHERE queue IN (1, 3)
            AND did IN ({','.join(map(str, dids))})
            """
        ) if dids else []
    
    rel_n, rel_l, lrn_n, lrn_l = 0, 0, 0, 0
    
    for card_did, due_val, left_val, card_state in db_data:

        steps = steps_setting(card_did, card_state)

        # intraday cards (queue=1) have dues stored as timestamps (=> > 10^9(s) = 20010909(iso))
        # interday cards (queue=3, not necessarily ivl > 1d as it depends on the day cutoff timestamp) have due = day count from epoch=0 (=> << 10^9(d))
        is_ready_now = (due_val <= current_time) if (due_val > 1000000000) else (due_val <= current_day)
        
        if (steps[-left_val] < 1440):
            # short-term card
            
            today_steps = short_term_count(steps[-left_val:]) - is_ready_now

            if (card_state == 3): # relearning
                rel_n += is_ready_now
                rel_l += today_steps
            else:
                lrn_n += is_ready_now
                lrn_l += today_steps
        else:
            # long-term card
            if is_ready_now:
                if (card_state == 3): # relearning
                    rel_n += 1
                else:
                    lrn_n += 1


    # fallback to the actual scheduler counts (changes nothing if SQL matches rust scheduler calculations)
    #   learn-ahead timer
    #   siblings to be auto-buried during the review
    #   parent deck review limits
    #   database request timeout
    sched_learn_total = max(0, tree.learn_count)
    if sched_learn_total == 0:
        rel_l += rel_n # change to 0?
        lrn_l += lrn_n # change to 0?
        rel_n = 0
        lrn_n = 0 
    else:
        total_db_now = rel_n + lrn_n
        if total_db_now > 0:
            rel_n = round(sched_learn_total * (rel_n / total_db_now))
            lrn_n = sched_learn_total - rel_n
        else:
            lrn_n = sched_learn_total


    # NEW cards
    new_n = 0
    new_l = 0

    root_dids = [d.id for d in mw.col.decks.all_names_and_ids() if "::" not in d.name] if is_db else [current_did]
    for root_did in root_dids:
        new_ni, new_li = deck_new_count(root_did)
        new_n += new_ni
        new_l += new_li

    lrn_l += round(new_l)

    
    # DONE cards
    done_total = mw.col.db.scalar(
            f"""
            SELECT count() FROM revlog
            WHERE id >= ? AND id < ?
            AND cid IN (SELECT id FROM cards WHERE did IN ({','.join(map(str, dids))}))
            """,
            int((cutoff - 86400) * 1000),
            int(cutoff * 1000)
        ) or 0 if dids else 0
    done = mw.col.db.first(
            f"""
            SELECT 
                -- 1. New (Total) = learning and has no prior reviews
                SUM(CASE WHEN r.type = 0 AND r.id = (SELECT MIN(id) FROM revlog WHERE cid = r.cid) THEN 1 ELSE 0 END),
                
                -- 2. Learning (Good/Easy), have prior review
                SUM(CASE WHEN r.type = 0 AND r.ease IN (3, 4) AND r.id != (SELECT MIN(id) FROM revlog WHERE cid = r.cid) THEN 1 ELSE 0 END),
                
                -- 3. Learning (Again/Hard), have prior review
                SUM(CASE WHEN r.type = 0 AND r.ease IN (1, 2) AND r.id != (SELECT MIN(id) FROM revlog WHERE cid = r.cid) THEN 1 ELSE 0 END),
                
                -- 4. Review (Total)
                SUM(CASE WHEN r.type = 1 THEN 1 ELSE 0 END),
                
                -- 5. Relearning (Good/Easy)
                SUM(CASE WHEN r.type = 2 AND r.ease IN (3, 4) THEN 1 ELSE 0 END),
                
                -- 6. Relearning (Again/Hard)
                SUM(CASE WHEN r.type = 2 AND r.ease IN (1, 2) THEN 1 ELSE 0 END)

                -- todo: type = 3 - filtered, type = 4 - manual (if reset -> new)
                
            FROM revlog r
            WHERE r.id >= ? AND r.id < ? 
            AND r.cid IN (SELECT id FROM cards WHERE did IN ({','.join(map(str, dids))}))
            """, 
            int((cutoff - 86400) * 1000), 
            int(cutoff * 1000)
        ) if dids else None
    done = [count or 0 for count in done] if done else [0] * 6
    

    # final count
    card_counts = [int(count) for count in ( done + [int(new_n), int(lrn_n), int(lrn_l), int(rev_n), int(rel_n), int(rel_l)])] 



    # upd display and log

    active_webview = None
    if mw.state == "review" and mw.reviewer.web:
        active_webview = mw.reviewer.web
    elif mw.state == "deckBrowser" and mw.deckBrowser.web:
        active_webview = mw.deckBrowser.web
    elif mw.state == "overview" and mw.overview.web:
        active_webview = mw.overview.web
    else:
        return

    # logging to JS console:
    log_msg = (
        f"Done: {done_total}={sum(card_counts[:6])} {card_counts[:6]}\\n"
        f"New: {card_counts[6]}\\n"
        f"Learn: {card_counts[7]}(+{card_counts[8]})\\n"
        f"Review: {card_counts[9]}\\n"
        f"Relearn: {card_counts[10]}(+{card_counts[11]})\\n"
    )
    active_webview.eval((
            # diff
            f"current = {card_counts};"
            f"past = JSON.parse(sessionStorage.getItem('cardCounts'));"
            f"sessionStorage.setItem('cardCounts', JSON.stringify(current));"
            f"if (past[0] !== current[0]) {{console.log(`+${{current[0]-past[0]}}`,'done (new)');}}"
            f"if (past[1] !== current[1]) {{console.log(`+${{current[1]-past[1]}}`,'done (learn)');}}"
            f"if (past[2] !== current[2]) {{console.log(`+${{current[2]-past[2]}}`,'done (learn | hold)');}}"
            f"if (past[3] !== current[3]) {{console.log(`+${{current[3]-past[3]}}`,'done (review)');}}"
            f"if (past[4] !== current[4]) {{console.log(`+${{current[4]-past[4]}}`,'done (relearn)');}}"
            f"if (past[5] !== current[5]) {{console.log(`+${{current[5]-past[5]}}`,'done (relearn | hold)');}}"
            f"if (past[6] !== current[6]) {{console.log(`${{current[6]>past[6]?'+':''}}${{current[6]-past[6]}}`,'new');}}"
            f"if (past[7] !== current[7] || past[8] !== current[8]) {{console.log(`${{current[7]>past[7]?'+':''}}${{current[7]-past[7]}}`,`(${{current[8]>past[8]?'+':''}}${{current[8]-past[8]}})`,'learn');}}"
            f"if (past[9] !== current[9]) {{console.log(`${{current[9]>past[9]?'+':''}}${{current[9]-past[9]}}`,'review');}}"
            f"if (past[10] !== current[10] || past[11] !== current[11]) {{console.log(`${{current[10]>past[10]?'+':''}}${{current[10]-past[10]}}`,`(${{current[11]>past[11]?'+':''}}${{current[11]-past[11]}})`,'relearn');}}"

            # result
            f"console.log('{log_msg}');"
        ))

    queries = [
        ".new.done",
        ".learn.done", ".learn.hold",
        ".review.done",
        ".re.learn.done", ".re.learn.hold",
        ".new.todo", 
        ".learn.todo", ".learn.future", 
        ".review.todo",
        ".re.learn.todo", ".re.learn.future"
        ]

    assign_stats_js = f"""
        (() => {{
            { "".join([f"""
                    (()=>{{
                        const segmL = document.querySelector('.lt-progress-segment{query}');
                        if(segmL) {{ 
                            segmL.style.setProperty('--count', {count});
                            if ({count} > 0) {{
                                // min-width -> 1px to avoid flex collapse
                                segmL.classList.remove('empty');
                            }} else {{
                                segmL.classList.add('empty');
                            }}
                            setTimeout(()=>segmL.classList.add('animated'),100);
                        }}
                    }})();
                """ for query, count in zip(queries, card_counts)]) }
        }})();
    """
    active_webview.eval(assign_stats_js)



def deck_main_inject(deck_view, content):
    content.tree += progress_bar_template()

def deck_view_inject(deck_view, content):
    content.table += progress_bar_template()

def reviewer_inject(web_content, context):
    if not isinstance(context, Reviewer): return
    web_content.body = progress_bar_template() + web_content.body

gui_hooks.deck_browser_will_render_content.append(deck_main_inject)
gui_hooks.overview_will_render_content.append(deck_view_inject)
gui_hooks.webview_will_set_content.append(reviewer_inject)

gui_hooks.reviewer_did_show_question.append(upd_progress)
gui_hooks.deck_browser_did_render.append(upd_progress)
gui_hooks.overview_did_refresh.append(upd_progress)
gui_hooks.state_did_change.append(upd_progress)