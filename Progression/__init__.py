import time
from aqt import mw, gui_hooks
from aqt.reviewer import Reviewer
from aqt.deckbrowser import DeckBrowser
from aqt.overview import Overview

# todo: merge reviews with new
#       [bug] ftr double counts
#   config colors
#
# filtered decks (unpack [left])
# manually scheduled cards
# non-monotonic learning steps?

def progress_bar_src():
    css = """
        :root {
            --prog-bg: #e0e0e0;
            --prog-done: yellowgreen;
            --prog-review: royalblue;
            --prog-relearn: red;
            --prog-learn: gold;
            --prog-new: forestgreen;
        }
        #lt-progress-bar {
            display: flex;
            width: 100vw;
            height: 5px;
            position: fixed;
            top: 0;
            left: 0;
            box-sizing: border-box;
            background-color: var(--prog-bg);
            z-index: 1000;
        }
        .lt-progress-segment {
            height: 100%;
            flex-basis: 0;
        }
        .lt-progress-segment.animated {
            transition: flex-grow 0.2s ease-in-out;
        }
        #prog-done { background-color: var(--prog-done); }
        #prog-rel-now { background-color: var(--prog-relearn); }
        #prog-lrn-now { background-color: var(--prog-learn); }
        #prog-rev { background-color: var(--prog-review); }
        #prog-new { background-color: var(--prog-new); }
        #prog-rel-ftr { background-color: var(--prog-relearn); opacity: 0.25; }
        #prog-lrn-ftr { background-color: var(--prog-learn); opacity: 0.25; }
    """
    
    html = """
        <div id="lt-progress-bar">
            <div id="prog-done" class="lt-progress-segment"></div>
            <div id="prog-rev" class="lt-progress-segment"></div>
            <div id="prog-rel-now" class="lt-progress-segment"></div>
            <div id="prog-lrn-now" class="lt-progress-segment"></div>
            <div id="prog-new" class="lt-progress-segment"></div>
            <div id="prog-rel-ftr" class="lt-progress-segment"></div>
            <div id="prog-lrn-ftr" class="lt-progress-segment"></div>
        </div>
    """
    return html, css

def deck_main_inject(deck_view, content):
    html, css = progress_bar_src()
    content.tree += f"<style>{css}</style>{html}"

def deck_view_inject(deck_view, content):
    html, css = progress_bar_src()
    content.table += f"<style>{css}</style>{html}"

def reviewer_inject(web_content, context):
    if not isinstance(context, Reviewer): return
    html, css = progress_bar_src()
    web_content.head += f"<style>{css}</style>"
    web_content.body = html + web_content.body


def total_intraday_steps(did, card_state: int):
    steps = [10.0] if card_state == 3 else [1.0, 10.0] # default anki steps
    try:
        config = mw.col.decks.config_dict_for_deck_id(did)
        if card_state == 3: # relearn
            steps = config.get("lapse", {}).get("delays", [10.0])
        else: # learn
            steps = config.get("new", {}).get("delays", [1.0, 10.0])
    except:
        pass

    return len([step for step in steps if step < 1440.0]) # count steps < 1d

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
                new_li += new_limited * total_intraday_steps(sub_did, 1)  

    # (NEW cards, future LEARN cards)
    return  (new_ni, new_li) 

def upd_progress(*args, **kwargs):
    if not mw.col or not mw.col.sched: return
    
    is_db = (mw.state == "deckBrowser")
    current_did = 0 if is_db else mw.col.decks.selected()
    tree = mw.col.sched.deck_due_tree(current_did)
    dids = [d["id"] for d in mw.col.decks.all()] if is_db else mw.col.decks.deck_and_child_ids(current_did)
    
    cutoff = mw.col.sched.day_cutoff
    live_now_secs = int(time.time())
    today_calendar_day = mw.col.sched.today
    
    # (RE)LEARN cards | queue = 1 - intraday, 3 - interday | type = 1 - learning, 3 - relearning
    db_data = mw.col.db.all(f"SELECT did, due, left, type FROM cards WHERE queue IN (1, 3) AND did IN ({','.join(map(str, dids))})") if dids else []
    
    rel_n, rel_l, lrn_n, lrn_l = 0, 0, 0, 0
    
    for card_did, due_val, left_val, type_val in db_data:
        
        if (due_val < 1000000000): # ivl < 1d cards (queue=1) have due storead as timestamps (=> >10^9(s) = 20010909(iso)), the rest (queue=3) have due = day count from epoch=0 (=> << 10^9)
            # interday card
            if (due_val <= today_calendar_day): # is_ready_now
                if (type_val == 3): # relearning
                    rel_n += 1
                else:
                    lrn_n += 1
        else:
            # intraday card
            is_ready_now = (due_val <= live_now_secs)
            current_step_idx = left_val // 1000 # left_val % 1000 = total_steps_today (does not count beyond day cutoff)
            future_intraday_steps = total_intraday_steps(card_did, type_val) - current_step_idx - is_ready_now

            if (type_val == 3): # relearning
                rel_n += is_ready_now
                rel_l += future_intraday_steps
            else:
                lrn_n += is_ready_now
                lrn_l += future_intraday_steps


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


    # REVIEW cards
    rev_n = max(0, tree.review_count)
    if not is_db:
        try: 
            rev_n = max(0, mw.col.sched.get_queued_cards().review_count) # returns count for the current deck
        except: 
            pass

    
    # DONE cards
    done = mw.col.db.scalar(f"SELECT count() FROM revlog WHERE id >= ? AND id < ? AND cid IN (SELECT id FROM cards WHERE did IN ({','.join(map(str, dids))}))", int((cutoff - 86400) * 1000), int(cutoff * 1000)) or 0 if dids else 0

    # final count
    card_counts = [int(done), int(rel_n), int(lrn_n), int(rev_n), int(new_n), int(rel_l), int(lrn_l)]



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
        f"Done: {card_counts[0]}\\n"
        f"Review: {card_counts[3]}\\n"
        f"Relearn: {card_counts[1]}(+{card_counts[5]})\\n"
        f"Learn: {card_counts[2]}(+{card_counts[6]})\\n"
        f"New: {card_counts[4]}"
    )
    active_webview.eval(f"console.log('{log_msg.replace("'", "\\'")}');")

    grow_values = card_counts if sum(card_counts) > 0 else [1, 0, 0, 0, 0, 0, 0]

    ids = ['prog-done', 'prog-rel-now', 'prog-lrn-now', 'prog-rev', 'prog-new', 'prog-rel-ftr', 'prog-lrn-ftr']
    
    js_pipeline = f"""
    (() => {{
        { '; '.join([f"var segmL = document.getElementById('{Lid}'); if(segmL) {{ segmL.style.flexGrow = {grow}; segmL.classList.add('animated'); }}" for Lid, grow in zip(ids, grow_values)]) }
    }})();
    """
    active_webview.eval(js_pipeline)


gui_hooks.deck_browser_will_render_content.append(deck_main_inject)
gui_hooks.overview_will_render_content.append(deck_view_inject)
gui_hooks.webview_will_set_content.append(reviewer_inject)

gui_hooks.reviewer_did_show_question.append(upd_progress)
gui_hooks.deck_browser_did_render.append(upd_progress)
gui_hooks.overview_did_refresh.append(upd_progress)
gui_hooks.state_did_change.append(upd_progress)