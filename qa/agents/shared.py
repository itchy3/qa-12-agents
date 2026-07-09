from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

# Resolve paths relative to this package by default so the share repo can run
# standalone. In the original project layout this file may also live under
# <project>/.hermes/qa/agents; in that case PROJECT becomes <project>.
QA_ROOT = Path(__file__).resolve().parents[1]
PROJECT = QA_ROOT.parents[1] if QA_ROOT.parent.name == '.hermes' else QA_ROOT.parent
ORIG_BASE = PROJECT / 'Originals/Cards/Conflict Encounter'
TRANS_BASE = PROJECT / '번역/카드 (Cards)/분쟁 조우 (Conflict Encounter)'
ORIG_BASES = [
    PROJECT / 'Originals/Cards/Conflict Encounter',
    PROJECT / 'Originals/Cards/Peaceful Encounter',
    PROJECT / 'Originals/Cards/Delve',
]
TRANS_BASES = [
    PROJECT / '번역/카드 (Cards)/분쟁 조우 (Conflict Encounter)',
    PROJECT / '번역/카드 (Cards)/평온 조우 (Peaceful Encounter)',
    PROJECT / '번역/카드 (Cards)/탐험 (Delve)',
]
POLICY = {'choice_xp':'omit_allowed','icon_markup':'format_tolerated_if_meaning_preserved','conditional_position':'description_or_battleObjective_allowed_if_scope_clear','auto_apply':False,'memory_update':'proposal_only_until_human_approval'}
PIPELINE_NAMES = ['카드 입력','Context pack 생성','원문 의미 패턴 추출','원문 슬롯 추출','번역문 슬롯 추출','용어집 검사','lore ontology 검사','patch note 검사','번역문 문형 지문 추출','구문사전/문형 규칙 검사','기존 번역 corpus에서 유사 문형 검색','기존 QA 로그와 비교','문형 일관성 판정','가독성 예외 여부 판단','룰 리스크 평가','수정안 생성','수정안 self-verification','최종 QA 판정','카드별 JSON QA 로그 저장','카드별 MD QA 리포트 저장','학습 반영 제안 생성','사람 승인 후 memory 업데이트']
AGENT_SEQUENCE = ['context-pack-builder','source-meaning-checker','terminology-manager','terminology-pattern-worker','syntax-pattern-controller','syntax-style-worker','inductive-style-learner','cross-card-consistency-checker','cross-card-pattern-worker','lore-ontology-checker','lore-ontology-worker','patch-note-checker','rules-lawyer','korean-editor','verifier','qa-reviewer','harness-meta-auditor']

CARD_FACTS: dict[str, dict[str, Any]] = {
 'GE-01': {'semantic_pattern':'CONFLICT_CHOICE_WITH_ENEMY_REPLACEMENT_AND_RACE_RANDOMIZATION','source_slots':{'choice1_action':['add level 5 Khajiit Marauder to EP','deploy it first','end of each round replace all other enemies with equal-level enemy units','maintain current HP','3+ players add level 10 Khajiit Cultist instead'],'choice1_battleObjective':'Conquer','choice2_action':['each adventurer must replace current race with different random race','current stats remain same'],'choice2_battleObjective':None,'choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_action':['레벨 5 카짓 습격자를 EP에 추가','가장 먼저 배치','각 라운드 끝 모든 다른 적을 같은 레벨 새 적 유닛으로 교체','현재 HP 유지','3+ 인 대신 레벨 10 카짓 광신도 추가'],'choice1_battleObjective':'전투 목표 - 정복','choice2_action':['각 모험가는 현재 종족을 무작위 다른 종족으로 교체','현재 능력치 동일 유지'],'choice2_battleObjective':None},'patterns':['At the end of each round','Each adventurer must','3+ players'],'issues':[{'issue_id':'GE-01-001','issue_type':'Typo/spacing','severity':'Note','span_ko':'추가 합니다','evidence':'용언 결합 띄어쓰기 오류. 룰 의미 변화는 없음.','suggested_fix':'추가합니다','confidence':0.99,'blocks_approval':False},{'issue_id':'GE-01-002','issue_type':'Terminology/Lore unresolved','severity':'Note','span_source':'Sheggorath','span_ko':'쉐고라스','evidence':'용어집에는 Sheggorath/Sheogorath 항목이 없음. 기존 번역이 맞을 가능성이 높지만 ontology 근거는 없음.','suggested_fix':'lore ontology에 Sheggorath=쉐고라스 항목 추가 후보로만 기록','confidence':0.55,'blocks_approval':False}], 'suggested_ko':'자동수정 없음. 확정 제안: “추가 합니다” → “추가합니다”. 나머지는 현행 유지 가능.','score':96,'verdict':'Suggestion'},
 'GE-02': {'semantic_pattern':'CONFLICT_CHOICE_WITH_DELVE_ROLL_TABLE_AND_PEACEFUL_D6_RESULT','source_slots':{'choice1_trigger':'after each time a new delve tile is revealed','choice1_action':'roll 1 D6 and resolve 1-2 nothing / 3 cache in unoccupied hex / 4-6 1 true damage to each enemy deployed to this tile and to each adventurer regardless of location','choice1_battleObjective':'Uncover','choice2_action':'each adventurer rolls 1 D6 and resolves result; 1-4 discard 1 item; 5-6 gain 1 bonus XP','choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_trigger':'새 델브 타일이 공개될 때마다','choice1_action':'D6 1개를 굴림; 1-2 없음 / 3 빈 헥스에 일반 아이템 보물 배치 / 4-6 이 타일에 배치된 각 적은 1 고정 피해를 받고, 각 모험가도 위치와 관계없이 1 고정 피해를 받음','choice1_battleObjective':'전투 목표 - 발굴','choice2_action':'각 모험가 D6 1개 굴리고 결과 해결; 1-4 아이템 1개 버림; 5-6 보너스 XP 1 획득'},'patterns':['After each time','roll 1 D6','Each adventurer rolls','true damage','unoccupied hex'],'issues':[{'issue_id':'GE-02-001','issue_type':'Terminology consistency','severity':'Major','span_source':'delve tile','span_ko':'델브 타일','evidence':'용어집 row 38: delve=탐험, row 121: tile=타일. “델브”는 승인 용어가 아니므로 “탐험 타일”이 기준.','suggested_fix':'새 탐험 타일이 공개될 때마다 D6 1개를 굴립니다.','confidence':0.93,'blocks_approval':True},{'issue_id':'GE-02-002','issue_type':'Terminology consistency','severity':'Major','span_source':'unoccupied hex','span_ko':'빈 헥스','evidence':'용어집 row 62: hex=칸, row 131 notes: unoccupied hex=빈 칸. “헥스”는 승인 용어와 충돌.','suggested_fix':'가능하다면 이 타일의 빈 칸 하나에 [[ICON_Common_Item|일반 아이템]] 보물 1개를 배치합니다.','confidence':0.95,'blocks_approval':True},{'issue_id':'GE-02-SCOPE-OK','issue_type':'Verified scope','severity':'Info','span_source':'1 true damage is dealt to each enemy deployed to this tile and to each adventurer regardless of location','span_ko':'이 타일에 배치된 각 적은 ... 각 모험가도 위치와 관계없이 ...','evidence':'regardless of location modifies adventurers; enemies remain limited to deployed to this tile. Current KO preserves scope.','suggested_fix':'수정 없음','confidence':0.96,'blocks_approval':False}], 'suggested_ko':'choice1 description에서 “델브 타일”→“탐험 타일”, “빈 헥스”→“빈 칸” 수정 권장. 낙반 4-6 문장은 현재 번역의 범위 해석이 맞으므로 수정 제안 없음.','score':78,'verdict':'Suggestion'},
 'GE-03': {'semantic_pattern':'CONFLICT_SINGLE_CHOICE_WITH_STATUS_DIE_AND_CONDITIONAL_OBJECTIVE','source_slots':{'choice1_action':['first enemy deployed must be Humanoid','each adventurer gains 3 light fatigue','then rolls status die and gains result','if Hex 1 enemy is last defeated, each adventurer draws 1 Legendary Item','Unstable: gain 4 light fatigue instead of 3','3+ players: Conquer'],'battleObjective':'Eliminate Hex 1 Enemy, modified to Conquer at 3+ players','choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_action':['가장 먼저 배치되는 적은 반드시 인간형','각 모험가는 가벼운 피로 3개 획득','그 후 상태 주사위 굴리고 결과 획득','1번 칸 적이 마지막으로 처치되면 각 모험가 전설 아이템 1장 뽑음','불안정: 3개 대신 4개','3+ 인: 정복'],'battleObjective':'전투 목표 - 1번 칸 적 제거; 3+ 인: 정복'},'patterns':['must be','Each adventurer gains','status die','If the Hex 1 enemy','Unstable:','3+ players'],'issues':[{'issue_id':'GE-03-001','issue_type':'Terminology/style unresolved','severity':'Note','span_source':'interrupt','span_ko':'저지합니다','evidence':'용어집 row 70: interrupt=개입. 여기서는 카드 선택지/서사 문장이라 “저지”도 자연스럽지만, 기계 효과 용어라면 “개입” 선호 가능.','suggested_fix':'기계적 용어로 고정할 필요가 있으면 “퍼즐 상자를 푸는 강도에게 개입합니다.” 후보. 현행 유지도 가능.','confidence':0.58,'blocks_approval':False},{'issue_id':'GE-03-002','issue_type':'im-not-ai/readability','severity':'Note','span_ko':'진귀한 광경 / 흘러나온 물건 / 불려 나올 리','evidence':'설명문 일부가 다소 문어적이지만 의미·룰·register를 해치지 않음. im-not-ai light 기준에서는 수정 필수 아님.','suggested_fix':'수정하지 않음. 사람 취향상 더 담백하게 원하면 description만 별도 윤문.','confidence':0.62,'blocks_approval':False}], 'suggested_ko':'현행 유지 가능. 용어집을 엄격 적용한다면 choice1 “저지합니다”만 “개입합니다” 후보이나, 서사/선택지 문맥에서는 수정 보류 권장.','score':94,'verdict':'Pass'}
 ,
 'GE-04': {'semantic_pattern':'CONFLICT_CACHE_SET_ASIDE_REWARD_AND_FATIGUE_CHOICE','source_slots':{'choice1_action':['after each cache unlocked, may discard item instead set aside cache chip','if successful reward is 1 XP per set-aside cache','3+ players remove 1 common item'],'choice1_battleObjective':'Conquer','choice2_action':['each adventurer must choose gain 3 or 6 light fatigue','if all choose 6 light fatigue add 1 XP to encounter rewards'],'choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_action':['보물을 열 때마다 얻은 아이템을 버리고 해당 보물 칩을 따로 둠','성공하면 따로 둔 보물 칩 1개마다 1 XP 획득','3+ 인 일반 아이템 1개 제거'],'choice1_battleObjective':'전투 목표 - 정복','choice2_action':['각 모험가는 가벼운 피로 3개 또는 6개 선택해 얻음','모든 모험가가 6개 선택 시 조우 보상에 1 XP 추가']},'patterns':['After each time a cache is unlocked','set aside the cache chip','Each adventurer must choose','3+ players'],'issues':[], 'suggested_ko':'현행 유지 가능. 3+ 인 위치는 battleObjective에 있어도 의미 보존.', 'score':97, 'verdict':'Pass'},
 'GE-05': {'semantic_pattern':'CONFLICT_DEFEAT_ENEMY_HEAL_AND_OPTIONAL_D6_TENACITY','source_slots':{'choice1_action':['after each time an adventurer defeats an enemy, that adventurer may heal 1 HP','Unstable: cannot retreat until at least 2 skyshards gained'],'choice1_battleObjective':'Uncover','choice2_action':['each adventurer may choose gain 2 light fatigue to roll 1 D6','1-3 gain 5 tenacity','4-6 nothing happens'],'choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_action':['모험가가 적을 처치한 후 1 HP 회복 가능','불안정: 스카이샤드 최소 2개 얻을 때까지 후퇴 불가'],'choice1_battleObjective':'전투 목표 - 발굴','choice2_action':['각 모험가는 가벼운 피로 2개를 얻기로 선택 가능, 그러면 D6 1개 굴리고 결과 해결','1-3 5 끈기 획득','4-6 아무 일도 없음']},'patterns':['After each time an adventurer defeats an enemy','that adventurer may heal','Unstable:','roll 1 D6','tenacity'],'issues':[{'issue_id':'GE-05-001','issue_type':'Scope/subject clarity','severity':'Note','span_source':'that adventurer may heal for 1 HP','span_ko':'모험가가 적을 처치한 후, 1 HP를 회복할 수 있습니다.','evidence':'영어는 처치한 바로 그 모험가(that adventurer)가 회복한다. 한국어는 주어 생략으로 보통 같은 모험가로 읽히지만, 명시성은 약간 낮음.','suggested_fix':'모험가가 적을 처치할 때마다, 그 모험가는 1 HP를 회복할 수 있습니다.','confidence':0.72,'blocks_approval':False}], 'suggested_ko':'현행 유지 가능. 더 명확히 하려면 “그 모험가”를 보강 후보로 검토.', 'score':94, 'verdict':'Suggestion'},
 'GE-06': {'semantic_pattern':'CONFLICT_ITEM_LOCKOUT_CACHE_RECOVERY_AND_LOSS_CHOICE','source_slots':{'choice1_action':['during this clash adventurers cannot use items','first time an adventurer unlocks a cache, discard item and place cache chip on their character mat','that adventurer recovered gear and can use items for remainder of battle','3+ players replace all common items with legendary items'],'choice1_battleObjective':'Conquer','choice2_action':['each adventurer must either discard all items or lose all tenacity','to do so must have at least 1 item or tenacity to lose'],'choice_rewards':'ignored_by_policy'},'ko_slots':{'choice1_action':['이번 격돌 동안 모험가들은 아이템 사용 불가','각 모험가가 처음 보물을 열면 그 아이템은 버리고 해당 보물 칩을 모험가 매트에 놓음','그 모험가는 장비를 되찾은 것으로 간주하고 전투 끝까지 아이템 사용 가능','3+ 인 모든 일반 아이템을 전설 아이템으로 교체'],'choice1_battleObjective':'전투 목표 - 정복','choice2_action':['각 모험가는 모든 아이템을 버리거나 모든 끈기를 잃어야 함','그렇게 하려면 버릴 아이템 또는 잃을 끈기 최소 1개 이상 필요']},'patterns':['During this clash','cannot use items','first time an adventurer unlocks a cache','character mat','3+ players','either discard all items or lose all tenacity'],'issues':[{'issue_id':'GE-06-001','issue_type':'Formatting/spacing','severity':'Note','span_ko':'[[ICON_Common_Item|일반 아이템]] 을 [[ICON_Legendary_Item|전설 아이템]] 으로','evidence':'아이콘 마크업 뒤 조사 앞 공백이 남아 있음. 의미 변화는 없으나 최종 원고 품질 이슈.','suggested_fix':'모든 [[ICON_Common_Item|일반 아이템]]을 [[ICON_Legendary_Item|전설 아이템]]으로 교체합니다.','confidence':0.99,'blocks_approval':False},{'issue_id':'GE-06-002','issue_type':'Terminology/style unresolved','severity':'Note','span_source':'character mat','span_ko':'모험가 매트','evidence':'용어집에 character mat 항목이 없어 확정 용어 판단 불가. “모험가 매트”는 이해 가능하지만 용어 승인 후보 필요.','suggested_fix':'용어집에 character mat=모험가 매트 후보 등록 또는 기존 공식 용어 확인.','confidence':0.55,'blocks_approval':False}], 'suggested_ko':'의미상 큰 문제 없음. 확정 수정 후보: 3+ 인 줄의 조사 앞 공백 제거. character mat 용어는 사람 확인 필요.', 'score':93, 'verdict':'Suggestion'}
}

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()] if path.exists() else []

def parse_sections(text: str) -> dict[str, list[str]]:
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3: text = parts[2]
    keys={'encounterName','description','choice1','choiceType','battleObjective','choice2'}; cur=None; buf=[]; out=[]
    for line in text.splitlines():
        if line.strip() in keys:
            if cur is not None: out.append((cur,'\n'.join(buf).strip()))
            cur=line.strip(); buf=[]
        elif cur is not None: buf.append(line)
    if cur is not None: out.append((cur,'\n'.join(buf).strip()))
    d={}
    for k,v in out: d.setdefault(k,[]).append(v)
    return d


def parse_ordered_sections(text: str) -> list[tuple[str, str]]:
    if text.startswith('---'):
        parts = text.split('---', 2)
        if len(parts) >= 3:
            text = parts[2]
    keys={'encounterName','description','choice1','choiceType','battleObjective','choice2'}
    cur=None; buf=[]; out=[]
    for line in text.splitlines():
        if line.strip() in keys:
            if cur is not None:
                out.append((cur,'\n'.join(buf).strip()))
            cur=line.strip(); buf=[]
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out.append((cur,'\n'.join(buf).strip()))
    return out

def _choice_scoped_sections(text: str) -> list[dict[str, str]]:
    ordered = parse_ordered_sections(text)
    choices: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for key, value in ordered:
        if key in ['choice1', 'choice2']:
            current = {'choice_key': key, 'choice_text': value}
            choices.append(current)
        elif current is not None and key in ['choiceType', 'description', 'battleObjective']:
            if key in current and current[key]:
                current[key] += '\n' + value
            else:
                current[key] = value
    return choices

def term_hits_for(source_text: str, ko_text: str) -> list[dict[str, Any]]:
    s=source_text.lower(); hits=[]
    for t in read_jsonl(QA_ROOT/'memory/term_db.jsonl'):
        en=str(t.get('en','')).strip().lower(); ko=str(t.get('ko','')).strip()
        probes={en,en.replace('(s)',''),en.replace('~','').strip(),en.replace('(주사위)','').strip()}
        if en and any(p and re.search(r'(?<![a-z])'+re.escape(p)+r'(?![a-z])',s) for p in probes): hits.append(t)
        elif ko and ko in ko_text: hits.append(t)
    seen=set(); out=[]
    for h in hits:
        key=h.get('sheet_row') or json.dumps(h,ensure_ascii=False,sort_keys=True)
        if key not in seen: out.append(h); seen.add(key)
    return out[:30]

def syntax_hits_for(source_text: str, ko_text: str) -> list[dict[str, Any]]:
    s=source_text.lower(); hits=[]
    for r in read_jsonl(QA_ROOT/'memory/syntax_rules.jsonl'):
        pat=str(r.get('source_pattern','')).lower().strip(); kt=str(r.get('ko_template','')); kp=kt.replace('X','').replace('~','').strip()
        if pat and (pat.replace('x','').strip() in s or pat in s): hits.append(r)
        elif kp and kp in ko_text: hits.append(r)
    return hits[:10]

def similar_corpus(patterns: list[str], exclude: str) -> list[dict[str, Any]]:
    results=[]
    for op in sorted(ORIG_BASE.glob('*.md')):
        code=op.name.split(' - ')[0]
        if code==exclude: continue
        txt=op.read_text(encoding='utf-8'); matched=[p for p in patterns if p.lower() in txt.lower()]
        if not matched: continue
        trans=list(TRANS_BASE.glob(code+' - *.md')); ko_lines=[]
        if trans:
            k=trans[0].read_text(encoding='utf-8')
            ko_lines=[line for line in k.splitlines() if any(x in line for x in ['각 모험가','배치','굴','불안정','3+ 인','라운드','고정 피해','가벼운 피로','전투 목표'])]
        src_lines=[line for line in txt.splitlines() if any(p.lower() in line.lower() for p in matched)]
        results.append({'item_id':f'Conflict Encounter/{code}','matched_patterns':matched,'source_lines':src_lines[:3],'ko_lines':ko_lines[:6],'trust':'observed_example'})
        if len(results)>=8: break
    return results

def find_card_files(code: str) -> tuple[Path, Path]:
    orig_matches=[]
    trans_matches=[]
    for base in ORIG_BASES:
        orig_matches.extend(sorted(base.glob(code+' - *.md')))
    for base in TRANS_BASES:
        trans_matches.extend(sorted(base.glob(code+' - *.md')))
    # Known workbook filename typo: Future Imperfect has Code: GE-15 in frontmatter but filename starts GE-13.
    if not trans_matches and code == 'GE-15':
        for base in TRANS_BASES:
            trans_matches.extend(sorted(base.glob('*Future Imperfect*.md')))
            trans_matches.extend(sorted(base.glob('*불완전한 미래*.md')))
    if not orig_matches:
        raise FileNotFoundError(f'No source card file found for {code}')
    orig = orig_matches[0]
    title = orig.stem.split(' - ', 1)[1] if ' - ' in orig.stem else ''
    if title and len(trans_matches) > 1:
        titled = [p for p in trans_matches if title in p.name]
        if titled:
            trans_matches = titled
    if not trans_matches and code == 'GE-15':
        for base in TRANS_BASES:
            trans_matches.extend(sorted(base.glob('*Future Imperfect*.md')))
            trans_matches.extend(sorted(base.glob('*불완전한 미래*.md')))
    if title and len(trans_matches) > 1:
        titled = [p for p in trans_matches if title in p.name]
        if titled:
            trans_matches = titled
    if not trans_matches:
        raise FileNotFoundError(f'No translation card file found for {code}')
    return orig, trans_matches[0]

def seeded_range_facts(code: str) -> dict[str, Any] | None:
    seeds: dict[str, dict[str, Any]] = {
        'GE-07': {'semantic_pattern':'CONFLICT_UNSTABLE_BEAST_DEPLOYMENT_OR_POOL_RECOVERY','source_slots':{'choice1':['Unstable Clash','use as many Beast enemies as possible','flip Party Size level 1 enemies to level 5 without adjusting HP','3+ players enemies deploy with +2 HP'],'battleObjective':'Conquer','choice2':['remove all fatigue from cooldown track','heal to full HP','gain 1 tenacity']},'ko_slots':{'choice1':['격돌 아이콘으로 번역됨','가능한 한 많은 야수 적 사용','파티 크기만큼 레벨 1 적을 HP 유지한 채 레벨 5 면으로 뒤집음','3+ 인 적 +2 HP 배치'],'battleObjective':'전투 목표 - 정복','choice2':['쿨다운 트랙 모든 피로 제거','최대 HP 회복','1 끈기 획득']},'patterns':['Unstable Clash','Beast enemies','without adjusting their HP','3+ players','remove all fatigue'],'issues':[{'issue_id':'GE-07-001','issue_type':'Choice type/icon mismatch','severity':'Major','span_source':'[[ICON_Unstable_Clash]] Hunt the beasts.','span_ko':'[[ICON_Clash|격돌]] 짐승들을 사냥합니다.','evidence':'원문 choice1/choiceType은 Unstable Clash인데 번역은 일반 Clash 아이콘으로 되어 있어 불안정 선택지 정보가 사라짐.','suggested_fix':'choice1 및 choiceType을 [[ICON_Unstable_Clash|불안정 격돌]] 계열로 수정 필요. 실제 프로젝트 아이콘 표기는 기존 규칙 확인 후 적용.','confidence':0.97,'blocks_approval':True},{'issue_id':'GE-07-002','issue_type':'Formatting/spacing','severity':'Note','span_ko':'+2 HP 로','evidence':'조사 앞 공백. 의미 변화는 없음.','suggested_fix':'+2 HP로','confidence':0.99,'blocks_approval':False}], 'suggested_ko':'choice1/choiceType의 Unstable Clash 아이콘 복구 필요. +2 HP로 띄어쓰기 정리 후보.', 'score':82,'verdict':'Needs revision'},
        'GE-08': {'semantic_pattern':'CONFLICT_ENDLESS_SKELETON_REDEPLOY_OR_DELAYED_ENEMY','source_slots':{'choice1':['set aside Skeleton/Skeleton Mage','on tile reveal replace first level 1/5 enemy deployment if possible','defeated skeleton is set aside not defeated enemy stack','if already in play deploy random enemy with +Party Size HP'],'battleObjective':'Uncover','choice2':['place level 5 Skeleton Mage on card','3+ place level 20 Bone Colossus HR instead','Persistent next clash add enemy to EP and deploy first']},'ko_slots':{'choice1':['레벨 1/5 스켈레톤/스켈레톤 마법사 따로 둠','타일 공개 때 첫 레벨 1/5 적 대신 해당 적 배치','처치되면 더미 대신 따로 둠','이미 플레이 중이면 무작위 적 +파티 크기 HP'],'battleObjective':'전투 목표 - 발굴','choice2':['레벨 5 스켈레톤 마법사 카드 위','3+이면 레벨 20 뼈의 거상 HR','다음 격돌 시작 시 EP 추가 후 먼저 배치']},'patterns':['Set aside level 1/5','Each time a tile is revealed','instead of on the defeated enemy stack','Persistent'],'issues':[{'issue_id':'GE-08-001','issue_type':'Formatting/spacing','severity':'Note','span_ko':'뼈의 거상 (HR) 을','evidence':'괄호 뒤 조사 앞 공백. 의미 변화 없음.','suggested_fix':'뼈의 거상 (HR)을','confidence':0.99,'blocks_approval':False}], 'suggested_ko':'의미상 큰 문제 없음. “(HR) 을” 띄어쓰기만 정리 후보.', 'score':96,'verdict':'Suggestion'},
        'GE-09': {'semantic_pattern':'CONFLICT_LOCKPICK_BENEFIT_OR_UNMODIFIED_CLASH','source_slots':{'choice1':['add level 5 enemy to EP and deploy last','adventurers may ignore 1 digit of choice on all lockpick checks'],'choice2':['carry out clash with no modifications','3+ players first option add level 10 enemy instead'],'battleObjective':'Conquer'},'ko_slots':{'choice1':['레벨 5 적 하나 EP 추가 마지막 배치','모든 자물쇠 따기에서 원하는 숫자 1개 무시 가능'],'choice2':['격돌 아무 변경 없이 진행','3+ 인 첫 번째 선택지 레벨 10 적 추가'],'battleObjective':'전투 목표 - 정복'},'patterns':['Add a level 5 enemy','deploy it last','ignore 1 digit','lockpick checks','3+ players'],'issues':[{'issue_id':'GE-09-001','issue_type':'Terminology/style clarity','severity':'Note','span_source':'on all lockpick checks','span_ko':'모든 자물쇠 따기에서','evidence':'의미는 전달되지만 check=판정이 명시되지 않음. 기존 용어가 “판정” 중심이면 “자물쇠 따기 판정”이 더 안전.','suggested_fix':'모든 자물쇠 따기 판정에서 원하는 숫자 1개를 무시할 수 있습니다.','confidence':0.72,'blocks_approval':False}], 'suggested_ko':'현행 의미 보존. “자물쇠 따기 판정”으로 명시성 보강 후보.', 'score':94,'verdict':'Suggestion'},
        'GE-10': {'semantic_pattern':'CONFLICT_CACHE_PROTECTION_ENEMY_MOVEMENT_DAMAGE','source_slots':{'choice1':['adventurers cannot move onto caches','enemies move toward closest cache and attempt to move onto it','after enemy moves onto cache each adventurer takes 1 true damage and cache discarded','3+ players place a cache on each hex'],'battleObjective':'Conquer'},'ko_slots':{'choice1':['모험가들은 보물이 있는 칸 이동 불가','적들은 가장 가까운 보물을 향해 이동하고 가능하면 그 칸으로 이동','적이 보물 칸으로 이동 후 각 모험가 1 고정 피해 및 보물 버림','3+ 인 [별] 칸마다 보물 배치'],'battleObjective':'전투 목표 - 정복'},'patterns':['cannot move onto caches','closest cache','each adventurer is dealt 1 true damage','each hex'],'issues':[{'issue_id':'GE-10-001','issue_type':'Scope/board-location mismatch','severity':'Major','span_source':'Place a cache on each hex.','span_ko':'[별] 칸마다 보물을 배치합니다.','evidence':'원문은 each hex(각 칸)인데 번역은 [별] 칸으로 특정 아이콘/별 칸에 한정된 것처럼 읽힘. 범위 축소 가능성이 큼.','suggested_fix':'3+ 인: 각 칸마다 보물을 배치합니다.','confidence':0.92,'blocks_approval':True}], 'suggested_ko':'3+ 인 문장은 “[별] 칸”이 아니라 “각 칸”으로 수정 검토 필요.', 'score':76,'verdict':'Needs revision'},
        'GE-11': {'semantic_pattern':'PEACEFUL_UNSTABLE_ITEM_DONATION_REWARD_TABLE_OR_SOCIAL_CHECK','source_slots':{'choice1':['Unstable Peaceful','discard any number of items','roll that many D6 +2','gain all rewards up to combined value','Unstable discard at least 2 items if possible'],'choice2':['social check Party Size x3','on success draw 1 Common Item and remove all light fatigue']},'ko_slots':{'choice1':['평온 아이콘으로 번역됨','아이템 원하는 만큼 버림','버린 아이템 수 +2개만큼 D6','합계 이하 모든 보상 획득','불안정 문장 자체는 번역됨'],'choice2':['사회 판정 파티 크기 x3','성공 시 일반 아이템 1장 및 모든 가벼운 피로 제거']},'patterns':['Unstable Peaceful','any number of items','that many D6 +2','all rewards up to','social check'],'issues':[{'issue_id':'GE-11-001','issue_type':'Choice type/icon mismatch','severity':'Major','span_source':'[[ICON_Unstable_Peaceful]] Conjure up a deal.','span_ko':'[[ICON_Peaceful_Outcome|평온]] 마법같은 거래를 성사시킵니다.','evidence':'원문 choice1/choiceType은 Unstable Peaceful인데 번역은 일반 Peaceful Outcome 아이콘. 불안정 선택지 정보가 아이콘에서 누락됨.','suggested_fix':'choice1 및 choiceType을 [[ICON_Unstable_Peaceful|불안정 평온]] 계열로 수정 필요.','confidence':0.97,'blocks_approval':True}], 'suggested_ko':'choice1/choiceType의 Unstable Peaceful 아이콘 복구 필요.', 'score':82,'verdict':'Needs revision'},
        'GE-12': {'semantic_pattern':'PEACEFUL_SIDE_QUEST_PERSISTENT_TOWN_ACTION_LOCKOUT','source_slots':{'choice1':['Unstable Peaceful','draw side quest and place this card with it','after completed gain 3 XP during Reward Phase in addition to quest rewards','side quest gains Persistent: no inn town actions until complete/discarded'],'choice2':['lead on quarry; no rules text']},'ko_slots':{'choice1':['평온 아이콘으로 번역됨','사이드 퀘스트 1장 뽑고 함께 놓음','완수 후 보상 단계 때 3 XP 추가','완수하거나 버릴 때까지 여관 마을 행동 불가'],'choice2':['사냥꾼에게 표적 단서; rules text empty']},'patterns':['Unstable Peaceful','side quest','Reward Phase','Persistent','inn town actions'],'issues':[{'issue_id':'GE-12-001','issue_type':'Choice type/icon mismatch','severity':'Major','span_source':'[[ICON_Unstable_Peaceful]] Take him up on his offer.','span_ko':'[[ICON_Peaceful_Outcome|평온]] 그의 제안을 수락합니다.','evidence':'원문 choice1/choiceType은 Unstable Peaceful인데 번역은 일반 Peaceful Outcome 아이콘.','suggested_fix':'choice1 및 choiceType을 [[ICON_Unstable_Peaceful|불안정 평온]] 계열로 수정 필요.','confidence':0.97,'blocks_approval':True}], 'suggested_ko':'choice1/choiceType의 Unstable Peaceful 아이콘 복구 필요.', 'score':82,'verdict':'Needs revision'},
        'GE-13': {'semantic_pattern':'PEACEFUL_UNSTABLE_SKILL_DIE_EXHAUST_FOR_BONUS_HP_OR_OVERFATIGUE','source_slots':{'choice1':['Unstable Peaceful','must exhaust at least 1 available skill die if possible','may exhaust up to 5','gain 1 bonus HP per exhausted skill die','Unstable exhaust as many as possible up to 5'],'choice2':['gain 2 overfatigue','skip today Adventurers Rest step']},'ko_slots':{'choice1':['평온 아이콘으로 번역됨','가능하다면 스킬 주사위 최소 1개 고갈','최대 5개까지 고갈','고갈한 주사위마다 1 추가 HP','불안정 문장 번역됨'],'choice2':['극심한 피로 2개','오늘의 모험가 휴식 단계 건너뜀']},'patterns':['Unstable Peaceful','exhaust skill die','bonus HP','overfatigue','Adventurers Rest step'],'issues':[{'issue_id':'GE-13-001','issue_type':'Choice type/icon mismatch','severity':'Major','span_source':'[[ICON_Unstable_Peaceful]] You feel strong...','span_ko':'[[ICON_Peaceful_Outcome|평온]] 강해진 것 같습니다...','evidence':'원문 choice1/choiceType은 Unstable Peaceful인데 번역은 일반 Peaceful Outcome 아이콘.','suggested_fix':'choice1 및 choiceType을 [[ICON_Unstable_Peaceful|불안정 평온]] 계열로 수정 필요.','confidence':0.97,'blocks_approval':True},{'issue_id':'GE-13-002','issue_type':'Formatting/style','severity':'Note','span_ko':'이 때','evidence':'표준 표기는 “이때”. 의미 변화 없음.','suggested_fix':'이때','confidence':0.9,'blocks_approval':False}], 'suggested_ko':'choice1/choiceType의 Unstable Peaceful 아이콘 복구 필요. “이때” 표기 정리 후보.', 'score':81,'verdict':'Needs revision'},
        'GE-14': {'semantic_pattern':'PEACEFUL_CLASH_OR_UNSTABLE_CLASH_ENGAGE_STAMINA_RESTRICTION','source_slots':{'choice1':['Clash','first enemy deployed must be Humanoid'],'choice2':['Unstable Clash','first enemy deployed must be Humanoid','each time adventurer engages roll dice >= Stamina or cannot engage'],'battleObjective':'none in extracted source'},'ko_slots':{'choice1':['격돌','처음 배치 적 인간형'],'choice2':['격돌 아이콘으로 번역됨','처음 배치 적 인간형','교전할 때마다 기력 이상 주사위, 불가능하면 교전 불가'],'battleObjective':'전투 목표 - 정복 추가됨'},'patterns':['Unstable Clash','first enemy deployed','engages','Stamina stat'],'issues':[{'issue_id':'GE-14-001','issue_type':'Choice type/icon mismatch','severity':'Major','span_source':'[[ICON_Unstable_Clash]] This surprise attack...','span_ko':'[[ICON_Clash|격돌]] 기습 공격 때문에...','evidence':'원문 choice2/choiceType은 Unstable Clash인데 번역은 일반 Clash 아이콘.','suggested_fix':'choice2 및 choiceType을 [[ICON_Unstable_Clash|불안정 격돌]] 계열로 수정 필요.','confidence':0.97,'blocks_approval':True},{'issue_id':'GE-14-002','issue_type':'Added objective not in source','severity':'Major','span_source':'no battleObjective text in extracted source','span_ko':'전투 목표 - 정복','evidence':'현재 추출 원문에는 battleObjective가 없는데 번역에는 정복 목표가 추가됨. PDF/원본 추출 누락 가능성은 있으나 현 텍스트 기준으로는 규칙 추가 위험.','suggested_fix':'원본 PDF 확인 후 battleObjective 유지/삭제 결정. 현 텍스트만 기준이면 삭제 후보.','confidence':0.78,'blocks_approval':True}], 'suggested_ko':'choice2 Unstable Clash 아이콘 복구 필요. “전투 목표 - 정복”은 원본 PDF 확인 전까지 승인 보류.', 'score':70,'verdict':'Needs revision'},
        'GE-15': {'semantic_pattern':'PEACEFUL_OVERLAND_DECK_REORDER_OR_ENEMY_SKILL_IGNORE_PERSISTENT','source_slots':{'choice1':['look at top 3 cards of one overland deck','rearrange in chosen order'],'choice2':['draw 2 enemies from level 1/5 enemy bag','place both on card, one level 1 and one level 5','Persistent next non-quest battle shared skill ignored','same name different numbers count same skill','after battle discard card and return enemies']},'ko_slots':{'choice1':['오버랜드 덱 1개의 맨 위 카드 3장 확인','원하는 순서로 다시 놓음'],'choice2':['레벨 1/5 적 주머니에서 적 2개','하나는 레벨 1, 하나는 레벨 5','다음 퀘스트가 아닌 전투 동안 같은 스킬 무시','이름 같고 숫자만 다르면 같은 스킬','전투 후 카드 버리고 적 주머니로 되돌림']},'patterns':['overland decks','rearrange','level 1/5 enemy bag','Persistent','same name but different numbers'],'issues':[{'issue_id':'GE-15-001','issue_type':'File metadata/code mismatch','severity':'Major','span_ko':'filename/frontmatter Code: GE-13 for Future Imperfect translation','evidence':'원문은 GE-15 Future Imperfect인데 번역 파일 경로/frontmatter가 GE-13으로 되어 있음. 내용은 GE-15와 맞지만 자동 매칭/이관에서 GE-13과 충돌 가능.','suggested_fix':'번역 파일명과 frontmatter Code를 GE-15로 수정하고 GE-13 Illusions 파일과 충돌하지 않게 정리.','confidence':0.99,'blocks_approval':True}], 'suggested_ko':'번역 내용 자체는 대체로 보존. 파일명/frontmatter Code를 GE-15로 정리 필요.', 'score':88,'verdict':'Needs revision'},
        'GE-16': {'semantic_pattern':'PEACEFUL_TRAP_ENEMY_LEVEL_EP_REDUCTION_PENALTY_TABLE','source_slots':{'choice1':['draw random enemy of chosen level and place on card','Persistent next non-quest clash reduce calculated EP by enemy level minimum 1','take penalty by enemy level','level 1 light fatigue 1','level 5 light fatigue 2 and 1 true damage','level 10 light fatigue 3 and 2 true damage','level 20 overfatigue 1 and light fatigue 1, status die result, lose all tenacity','after battle discard and return enemy']},'ko_slots':{'choice1':['선택 레벨 무작위 적 하나 카드 위','다음 퀘스트가 아닌 격돌 시작 시 계산된 EP를 적 레벨만큼 최소 1까지 줄임','레벨별 페널티','레벨 1 가벼운 피로 1','레벨 5 가벼운 피로 2 및 1 고정 피해','레벨 10 가벼운 피로를 3개 및 2 고정 피해','레벨 20 극심한 피로 1, 가벼운 피로 1, 상태 주사위, 모든 끈기 상실','전투 후 카드 버리고 적 주머니 반환']},'patterns':['random enemy of the level','reduce the calculated EP','minimum of 1','corresponding penalty','overfatigue'],'issues':[{'issue_id':'GE-16-001','issue_type':'Typo/particle','severity':'Note','span_ko':'가벼운 피로를 3개를 얻고','evidence':'목적격 조사 중복. 의미 변화 없음.','suggested_fix':'가벼운 피로 3개를 얻고','confidence':0.99,'blocks_approval':False}], 'suggested_ko':'의미상 큰 문제 없음. 레벨 10 줄의 조사 중복만 정리 후보.', 'score':96,'verdict':'Suggestion'},
    }
    return seeds.get(code)


def _first_icon(text: str) -> str | None:
    m = re.search(r'\[\[(ICON_[A-Za-z0-9_]+)(?:\|[^\]]+)?\]\]', text)
    return m.group(1) if m else None

def _section_text(sections: dict[str, list[str]], name: str) -> str:
    vals = sections.get(name) or []
    return '\n'.join(v for v in vals if v).strip()

def _parse_rule_action(text: str, include_target: bool = True) -> dict[str, Any] | None:
    cleaned = re.sub(r'\s+', ' ', (text or '').strip()).rstrip('.')
    pattern = re.compile(r'(?:(each adventurer|that adventurer|each enemy|an enemy|you)\s+)?(?:(must|may|cannot|can)\s+)?(discard|discards|recover|recovers|gain|gains|draw|draws|place|places|deal|deals)\s+(\d+)\s+(.+?)(?:,?\s+if possible)?$', re.I)
    m = pattern.search(cleaned)
    if not m:
        return None
    target, modal, verb, amount, obj = m.groups()
    verb = {
        'discards': 'discard',
        'recovers': 'recover',
        'gains': 'gain',
        'draws': 'draw',
        'places': 'place',
        'deals': 'deal',
    }.get(verb.lower(), verb.lower())
    if obj.lower() in ['health']:
        obj = 'health'
    elif obj.lower() in ['item', 'items']:
        obj = 'item'
    elif obj.lower() in ['bonus xp', 'xp']:
        obj = 'bonus XP' if 'bonus' in obj.lower() else 'XP'
    action: dict[str, Any] = {}
    if include_target and target:
        action['target'] = target.lower()
    if modal:
        action['modal'] = modal.lower()
    action.update({'verb': verb.lower(), 'amount': amount, 'object': obj})
    return action


def _parse_rule_actions(text: str) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for part in re.split(r'\n|;|(?<=\.)\s+', text or ''):
        action = _parse_rule_action(part, include_target=True)
        if action:
            actions.append(action)
    return actions


def _parse_random_table(text: str) -> dict[str, Any] | None:
    if not re.search(r'rolls?\s+1\s+D6|roll\s+1\s+D6|rolled result', text or '', re.I):
        return None
    outcomes = []
    for rng, body in re.findall(r'(?m)^\s*(\d+\s*-\s*\d+)\s*:\s*(.+?)\s*$', text or ''):
        action = _parse_rule_action(body, include_target=False) or {'raw': body.strip()}
        action = {'range': rng.replace(' ', ''), **action}
        outcomes.append(action)
    table = {'die': 'D6', 'outcomes': outcomes}
    if re.search(r'each adventurer\s+rolls?', text or '', re.I):
        table['roller'] = 'each adventurer'
    return table if outcomes else None


def extract_source_slots_generic(source: str) -> tuple[str, dict[str, Any], list[str]]:
    whole = source
    scoped = _choice_scoped_sections(source)
    slots: dict[str, Any] = {}
    patterns: list[str] = []
    choices: list[dict[str, Any]] = []
    for choice in scoped:
        choice_text = choice.get('choice_text', '')
        choice_type_text = choice.get('choiceType', '') or choice_text
        description = choice.get('description', '')
        battle = choice.get('battleObjective', '')
        icon = _first_icon(choice_type_text) or _first_icon(choice_text)
        cslot: dict[str, Any] = {'choice_key': choice.get('choice_key', ''), 'choice_text': re.sub(r'\[\[[^\]]+\]\]', '', choice_text).strip()}
        if icon:
            cslot['choice_type_icon'] = icon
            patterns.append(icon)
        if battle:
            cslot['battle_objective'] = battle
            patterns.append('battleObjective')
        m = re.search(r'Unstable:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            cslot['unstable_effect'] = {'raw': m.group(1).strip()}
            patterns.append('Unstable')
        m = re.search(r'Persistent:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            raw = m.group(1).strip()
            cslot['persistent_effect'] = {'raw': raw}
            tm = re.search(r'(After each time|Each time|At the start|At the end|When)(.+?),\s*(.+)', raw, re.I)
            if tm:
                cslot['persistent_effect'].update({'timing': (tm.group(1)+tm.group(2)).strip(), 'effect': tm.group(3).strip()})
            patterns.append('Persistent')
        m = re.search(r'3\+\s*players:\s*(.+?)(?:\n|$)', description, re.I)
        if m:
            raw = m.group(1).strip()
            scope = 'each hex' if re.search(r'\b(each|every)\s+hex\b|\ball\s+hexes\b', raw, re.I) else 'unspecified'
            cslot['three_plus_players'] = {'raw': raw, 'scope': scope}
            patterns.append('3+ players')
        actions = _parse_rule_actions(description)
        if actions:
            cslot['actions'] = actions
            patterns.append('rule_actions')
        random_table = _parse_random_table(description)
        if random_table:
            cslot['random_table'] = random_table
            patterns.append('random_table')
        choices.append(cslot)
    if choices:
        slots['choices'] = choices
        first = choices[0]
        for key in ['choice_type_icon','choice_text','battle_objective','unstable_effect','persistent_effect','three_plus_players']:
            if key in first:
                slots[key] = first[key]
    if re.search(r'\beach adventurer\b', whole, re.I):
        slots.setdefault('targets', []).append('each adventurer')
        patterns.append('each adventurer')
    if re.search(r'\b(each|every)\s+hex\b|\ball\s+hexes\b', whole, re.I) and not any('three_plus_players' in c for c in choices):
        slots['board_scope'] = {'scope': 'each hex'}
        patterns.append('each hex')
    first_icon = choices[0].get('choice_type_icon') if choices else _first_icon(whole)
    any_unstable_icon = any(str(c.get('choice_type_icon','')).startswith('ICON_Unstable') for c in choices)
    any_persistent = any('persistent_effect' in c for c in choices)
    any_unstable_text = any('unstable_effect' in c for c in choices) or 'Unstable:' in whole
    any_each_hex_3p = any((c.get('three_plus_players') or {}).get('scope') == 'each hex' for c in choices)
    if first_icon == 'ICON_Unstable_Peaceful' and any_persistent:
        semantic = 'PEACEFUL_UNSTABLE_PERSISTENT_EFFECT'
    elif first_icon == 'ICON_Unstable_Clash' or any(str(c.get('choice_type_icon','')).startswith('ICON_Unstable_Clash') for c in choices):
        semantic = 'CONFLICT_UNSTABLE_CLASH_EFFECT'
    elif any_unstable_icon or any_unstable_text:
        semantic = 'UNSTABLE_CHOICE_EFFECT'
    elif any_each_hex_3p:
        semantic = 'THREE_PLUS_EACH_HEX_MODIFIER'
    elif slots:
        semantic = 'SECTIONED_ENCOUNTER_EFFECT'
    else:
        semantic = 'UNRESOLVED'
    return semantic, slots, patterns

def extract_ko_slots_generic(ko: str) -> dict[str, Any]:
    whole = ko
    scoped = _choice_scoped_sections(ko)
    slots: dict[str, Any] = {}
    choices: list[dict[str, Any]] = []
    for choice in scoped:
        choice_text = choice.get('choice_text', '')
        choice_type_text = choice.get('choiceType', '') or choice_text
        description = choice.get('description', '')
        icon = _first_icon(choice_type_text) or _first_icon(choice_text)
        cslot: dict[str, Any] = {'choice_key': choice.get('choice_key', ''), 'choice_text': re.sub(r'\[\[[^\]]+\]\]', '', choice_text).strip()}
        if icon:
            cslot['choice_type_icon'] = icon
        if choice.get('battleObjective') or '전투 목표' in choice.get('battleObjective',''):
            cslot['battle_objective'] = 'present'
        if '불안정' in description:
            line = next((x.strip() for x in description.splitlines() if '불안정' in x), '')
            cslot['unstable_effect'] = {'raw': line}
        if '지속:' in description or '지속' in description:
            line = next((x.strip() for x in description.splitlines() if x.strip().startswith('지속:')), '')
            if not line:
                line = next((x.strip() for x in description.splitlines() if '지속' in x), '')
            cslot['persistent_effect'] = {'raw': line}
        m = re.search(r'3\+\s*인[:：]?\s*(.+?)(?:\n|$)', description)
        if m:
            raw = m.group(1).strip()
            if '[별]' in raw or '별 칸' in raw:
                scope = 'star hex'
            elif '각 칸' in raw or '모든 칸' in raw:
                scope = 'each hex'
            else:
                scope = 'unspecified'
            cslot['three_plus_players'] = {'raw': raw, 'scope': scope}
        choices.append(cslot)
    if choices:
        slots['choices'] = choices
        first = choices[0]
        for key in ['choice_type_icon','choice_text','battle_objective','unstable_effect','persistent_effect','three_plus_players']:
            if key in first:
                slots[key] = first[key]
    if '전투 목표' in whole and 'battle_objective' not in slots:
        slots['battle_objective'] = 'present'
    return slots


def detect_source_quality_issues(code: str, source_slots: dict[str, Any]) -> list[dict[str, Any]]:
    """Log source/OCR/extracted-text anomalies without treating them as translation QA failures."""
    issues: list[dict[str, Any]] = []
    for idx, choice in enumerate(source_slots.get('choices', []) or [], start=1):
        persistent = choice.get('persistent_effect') if isinstance(choice, dict) else None
        raw = persistent.get('raw', '') if isinstance(persistent, dict) else ''
        marker = 'This persistent is not discarded at the end of a session.'
        if marker in raw:
            tail = raw.split(marker, 1)[1].strip()
            if tail:
                issues.append({
                    'issue_id': f'{code}-SOURCE_TEXT_LEAKAGE-001',
                    'issue_type': 'Source text extraction leakage',
                    'severity': 'SourceWarning',
                    'choice_key': choice.get('choice_key', f'choice{idx}'),
                    'span_source': tail,
                    'evidence': 'Source persistent text appears to include leaked narrative/next-choice text after the persistent-discard sentence.',
                    'suggested_action': 'Log only. Do not auto-fix source or translation; confirm against PDF/source if this affects QA interpretation.',
                    'blocks_translation_approval': False,
                    'requires_source_review': True,
                })
    return issues

def _slot_key_set(value: Any, prefix: str = '') -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            path = f'{prefix}.{k}' if prefix else str(k)
            keys.add(path)
            keys |= _slot_key_set(v, path)
    elif isinstance(value, list):
        for v in value:
            keys |= _slot_key_set(v, prefix)
    elif prefix:
        keys.add(prefix)
    return keys

def compare_seed_to_parser(seed_slots: dict[str, Any] | None, parser_slots: dict[str, Any]) -> dict[str, Any]:
    if not seed_slots:
        return {'coverage': 1.0 if parser_slots else 0.0, 'missing_from_parser': [], 'parser_extra': sorted(_slot_key_set(parser_slots)), 'seed_slot_count': 0, 'parser_slot_count': len(_slot_key_set(parser_slots))}
    seed_keys = _slot_key_set(seed_slots)
    parser_keys = _slot_key_set(parser_slots)
    family_hits = set()
    family_map = {
        'choice': ['choice_type_icon', 'choice_text'],
        'battleObjective': ['battle_objective'],
        'battle_objective': ['battle_objective'],
        'Persistent': ['persistent_effect'],
        'persistent': ['persistent_effect'],
        'Unstable': ['unstable_effect', 'choice_type_icon'],
        '3+': ['three_plus_players'],
        'three_plus': ['three_plus_players'],
    }
    for sk in seed_keys:
        for needle, pks in family_map.items():
            if needle.lower() in sk.lower() and any(pk in parser_keys for pk in pks):
                family_hits.add(sk)
    direct_hits = {sk for sk in seed_keys if any(part in parser_keys for part in sk.split('.'))}
    hits = family_hits | direct_hits
    # Broad family matches prove the parser saw the right section, not every seeded rule detail.
    # Keep the score conservative unless exact nested seed keys are covered.
    exact_hits = seed_keys & parser_keys
    denom = max(1, len(seed_keys))
    weighted_hits = (0.35 * len(family_hits)) + (0.65 * len(exact_hits)) + min(len(parser_keys), 3) * 0.15
    coverage = min(0.95 if len(exact_hits) < len(seed_keys) else 1.0, weighted_hits / denom)
    return {'coverage': round(coverage, 3), 'missing_from_parser': sorted(seed_keys - hits)[:30], 'parser_extra': sorted(parser_keys - seed_keys)[:30], 'seed_slot_count': len(seed_keys), 'parser_slot_count': len(parser_keys), 'exact_seed_key_hits': len(exact_hits), 'family_seed_key_hits': len(family_hits)}

def infer_parser_facts(card: dict[str, Any], seeded: dict[str, Any] | None = None) -> dict[str, Any]:
    source = str(card.get('source_text','')).strip()
    ko = str(card.get('current_ko','')).strip()
    code = str(card.get('card_id') or card.get('code') or 'CARD')
    semantic, source_slots, patterns = extract_source_slots_generic(source)
    ko_slots = extract_ko_slots_generic(ko)
    issues: list[dict[str, Any]] = []
    src_icon = source_slots.get('choice_type_icon')
    ko_icon = ko_slots.get('choice_type_icon')
    if src_icon and str(src_icon).startswith('ICON_Unstable') and (not ko_icon or not str(ko_icon).startswith('ICON_Unstable')):
        issues.append({'issue_id': f'{code}-REG_UNSTABLE_ICON_MISMATCH', 'issue_type': 'Choice type/icon mismatch', 'severity': 'Major', 'span_source': src_icon, 'span_ko': ko_icon or '', 'evidence': 'Source choice icon is unstable but KO choice icon is non-unstable or missing.', 'suggested_fix': f'Use {src_icon} unstable choice icon/choiceType in KO.', 'confidence': 0.94, 'blocks_approval': True})
    src_3p = source_slots.get('three_plus_players') if isinstance(source_slots.get('three_plus_players'), dict) else {}
    ko_3p = ko_slots.get('three_plus_players') if isinstance(ko_slots.get('three_plus_players'), dict) else {}
    source_quality_issues = detect_source_quality_issues(code, source_slots)
    if src_3p.get('scope') == 'each hex' and ko_3p.get('scope') == 'star hex' and '[' in str(ko_3p.get('raw', '')):
        source_quality_issues.append({
            'issue_id': f'{code}-SOURCE_ICON_CRAWL_MISSING-001',
            'issue_type': 'Source crawl/icon omission candidate',
            'severity': 'SourceWarning',
            'span_source': src_3p.get('raw', ''),
            'span_ko': ko_3p.get('raw', ''),
            'evidence': 'KO contains a bracketed board icon marker, while crawled/source text only says each/every hex. Treat as likely source crawl/OCR icon omission, not a translation scope failure.',
            'suggested_action': 'Log only. Confirm against PDF/source image; do not auto-fix translation from crawled English text alone.',
            'blocks_translation_approval': False,
            'requires_source_review': True,
        })
    elif src_3p.get('scope') == 'each hex' and ko_3p.get('scope') == 'star hex':
        issues.append({'issue_id': f'{code}-REG_EACH_HEX_SCOPE_NOT_STAR_HEX', 'issue_type': 'Scope/board-location mismatch', 'severity': 'Major', 'span_source': src_3p.get('raw',''), 'span_ko': ko_3p.get('raw',''), 'evidence': 'Source says each/every hex, but KO narrows the scope to star/special hex.', 'suggested_fix': '각 칸마다 보물을 배치합니다.', 'confidence': 0.92, 'blocks_approval': True})
    lower_source = source.lower()
    if re.search(r'\bmust\b', lower_source) and re.search(r'수 있|가능', ko):
        issues.append({'issue_id': f'{code}-REG_MODAL_MUST_TO_MAY', 'issue_type': 'Modal force mismatch', 'severity': 'Major', 'span_source': 'must', 'span_ko': '할 수 있습니다/가능', 'evidence': 'Source obligation uses must, but KO weakens it into optional may/can wording.', 'suggested_fix': 'must는 “해야 합니다/반드시 …합니다” 계열로 보존합니다.', 'confidence': 0.9, 'blocks_approval': True})
    if 'regardless of location' in lower_source and '위치와 관계없이' not in ko and '위치에 관계없이' not in ko:
        issues.append({'issue_id': f'{code}-REG_SCOPE_REGARDLESS_OMITTED', 'issue_type': 'Scope qualifier omission', 'severity': 'Major', 'span_source': 'regardless of location', 'span_ko': '', 'evidence': 'Source explicitly applies regardless-of-location scope, but KO omits the location-independent qualifier.', 'suggested_fix': '대상 문장에 “위치와 관계없이” 범위를 명시합니다.', 'confidence': 0.9, 'blocks_approval': True})
    if semantic == 'UNRESOLVED':
        issues.append({'issue_id':f'{code}-UNRESOLVED-001','issue_type':'Unresolved Pattern','severity':'Major','evidence':'Generic parser could not infer semantic pattern.','suggested_fix':'사람 검토 필요','confidence':0.2,'blocks_approval':True})
    seed_issues = list(seeded.get('issues', [])) if seeded else []
    seen = {i.get('issue_id') for i in issues}
    for issue in seed_issues:
        if issue.get('issue_id', '').endswith('REG_EACH_HEX_SCOPE_NOT_STAR_HEX') or issue.get('issue_type') == 'Scope/board-location mismatch':
            if '[' in str(issue.get('span_ko', '')):
                source_quality_issues.append({
                    'issue_id': f'{code}-SOURCE_ICON_CRAWL_MISSING-001',
                    'issue_type': 'Source crawl/icon omission candidate',
                    'severity': 'SourceWarning',
                    'span_source': issue.get('span_source', ''),
                    'span_ko': issue.get('span_ko', ''),
                    'evidence': 'Seeded board-scope mismatch includes a bracketed KO icon marker. Per policy, bracketed KO icons can indicate English crawl/source icon omission rather than translation scope narrowing.',
                    'suggested_action': 'Log only. Confirm against PDF/source image; do not auto-fix translation from crawled English text alone.',
                    'blocks_translation_approval': False,
                    'requires_source_review': True,
                })
                continue
        if issue.get('issue_id') not in seen:
            issues.append(issue)
    quality = compare_seed_to_parser(seeded.get('source_slots') if seeded else None, source_slots)
    has_major = any(i.get('severity') in ['Critical','Major'] for i in issues)
    has_note = any(i.get('severity') in ['Note','Minor','Info'] for i in issues)
    score = seeded.get('score') if seeded else (82 if has_major else 94 if has_note else 97)
    verdict = seeded.get('verdict') if seeded else ('Needs revision' if has_major else 'Suggestion' if has_note else 'Pass')
    suggested = seeded.get('suggested_ko') if seeded else ('사람 검토 필요' if has_major else '현행 유지 가능.')
    return {'mode':'parser_extracted','semantic_pattern':semantic,'source_slots':source_slots,'ko_slots':ko_slots,'expected_seed_slots':seeded.get('source_slots') if seeded else None,'slot_extraction_quality':quality,'patterns':patterns,'issues':issues,'source_quality_issues':source_quality_issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':'parser_extracted','expected_template':'seed_or_parser_rules','rule_source':'generic_parser+seed_oracle' if seeded else 'generic_parser'}

def infer_generic_facts(card: dict[str, Any]) -> dict[str, Any]:
    source = str(card.get('source_text','')).strip()
    ko = str(card.get('current_ko','')).strip()
    code = str(card.get('card_id') or card.get('code') or '')
    seeded = seeded_range_facts(code)
    if seeded:
        return infer_parser_facts(card, seeded)
    # Prefer the section-aware generic encounter parser for real card text.
    if any(token in source for token in ['choice1', 'choiceType', 'battleObjective', 'Persistent:', 'Unstable:', '3+ players', '[[ICON_']):
        parsed = infer_parser_facts(card, None)
        if parsed['semantic_pattern'] != 'UNRESOLVED':
            return parsed
    prior = card.get('prior_translations') or []
    approved_logs = card.get('approved_qa_logs') or []
    lower = source.lower()
    issues: list[dict[str, Any]] = []
    suggested = '현행 유지 가능.'
    score = 100
    verdict = 'Pass'
    patterns: list[str] = []

    if re.search(r'attack an enemy .*times?\.?$', lower):
        m = re.search(r'attack an enemy (.+?times?)\.?$', lower)
        count = m.group(1) if m else 'multiple times'
        semantic = 'REPEAT_ACTION_COUNT'
        slots = {'action':'Attack','target':'an enemy','count':count}
        ko_slots = {'action':'공격','target':'적 하나','count':('세 번' if 'three' in count else count)}
        actual = '{횟수}번 {대상}을 {행동}합니다.' if re.match(r'\s*\d+번\s+', ko) else '{대상}을 {횟수}번 {행동}합니다.'
        expected = None
        for log in approved_logs:
            if log.get('semantic_pattern') == semantic and log.get('ko_template'):
                expected = log['ko_template']; break
        expected = expected or '{대상}을 {횟수}번 {행동}합니다.'
        if actual != expected:
            suggested = '적 하나를 세 번 공격합니다.' if 'three' in count else ko
            issues.append({'issue_id':f"{card.get('card_id','CARD')}-SYNTAX-001",'issue_type':'Syntax Pattern Consistency','severity':'Major','evidence':f"기존 승인 문형은 '{expected}'이나 현재 번역은 '{actual}' 구조.",'suggested_fix':suggested,'confidence':0.92,'blocks_approval':True})
            score = 92; verdict = 'Pass with fixes'
        patterns = ['Attack an enemy', 'times']
        return {'semantic_pattern':semantic,'source_slots':slots,'ko_slots':ko_slots,'patterns':patterns,'issues':issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':actual,'expected_template':expected,'rule_source':'approved_qa_logs' if approved_logs else 'dominant_observed'}

    if re.search(r'deal\s+\d+\s+damage\.?$', lower):
        m = re.search(r'deal\s+(\d+)\s+damage', lower); amount = m.group(1) if m else ''
        semantic = 'DEAL_DAMAGE_AMOUNT'
        slots = {'action':'Deal damage','amount':amount}
        ko_slots = {'action':'피해를 줌','amount':amount}
        actual = '피해 {수량}을 줍니다.' if re.search(r'피해\s*\d+을\s*줍니다', ko) else '{수량} 피해를 줍니다.'
        expected = '{수량} 피해를 줍니다.'
        if actual != expected:
            suggested = f'{amount} 피해를 줍니다.'
            issues.append({'issue_id':f"{card.get('card_id','CARD')}-SYNTAX-001",'issue_type':'Syntax Pattern Consistency','severity':'Major','evidence':f"같은 피해량 부여 패턴에서 수량 위치와 조사 구조가 기존 승인 문형과 다름. expected={expected}, actual={actual}",'suggested_fix':suggested,'confidence':0.92,'blocks_approval':True})
            score = 92; verdict = 'Pass with fixes'
        patterns = ['Deal', 'damage']
        return {'semantic_pattern':semantic,'source_slots':slots,'ko_slots':ko_slots,'patterns':patterns,'issues':issues,'suggested_ko':suggested,'score':score,'verdict':verdict,'actual_template':actual,'expected_template':expected,'rule_source':'dominant_prior_translations'}

    simple_actions = _parse_rule_actions(source)
    if simple_actions:
        issues = []
        if re.search(r'\bmust\b', lower) and re.search(r'수 있|가능', ko):
            issues.append({'issue_id': f"{card.get('card_id','CARD')}-REG_MODAL_MUST_TO_MAY", 'issue_type': 'Modal force mismatch', 'severity': 'Major', 'span_source': 'must', 'span_ko': '할 수 있습니다/가능', 'evidence': 'Source obligation uses must, but KO weakens it into optional may/can wording.', 'suggested_fix': 'must는 “해야 합니다/반드시 …합니다” 계열로 보존합니다.', 'confidence': 0.9, 'blocks_approval': True})
        return {'mode': 'parser_extracted', 'semantic_pattern': 'SIMPLE_ACTION_EFFECT', 'source_slots': {'actions': simple_actions}, 'ko_slots': {'raw': ko}, 'patterns': ['rule_actions'], 'issues': issues, 'suggested_ko': '사람 검토 필요' if issues else '현행 유지 가능.', 'score': 82 if issues else 97, 'verdict': 'Needs revision' if issues else 'Pass', 'actual_template': 'parser_extracted', 'expected_template': 'parser_extracted', 'rule_source': 'generic_parser'}

    return {'semantic_pattern':'UNRESOLVED','source_slots':{'raw':source},'ko_slots':{'raw':ko},'patterns':source.split()[:5],'issues':[{'issue_id':f"{card.get('card_id','CARD')}-UNRESOLVED-001",'issue_type':'Unresolved Pattern','severity':'Major','evidence':'Generic analyzer could not infer semantic pattern.','suggested_fix':'사람 검토 필요','confidence':0.2,'blocks_approval':True}],'suggested_ko':'사람 검토 필요','score':50,'verdict':'Needs revision','actual_template':'unresolved','expected_template':'unresolved','rule_source':'unresolved'}

def new_context_from_card(card: dict[str, Any], run_id: str) -> dict[str, Any]:
    code = str(card.get('card_id') or card.get('code') or 'CARD_UNKNOWN')
    facts = infer_generic_facts(card)
    source = str(card.get('source_text',''))
    current_ko = str(card.get('current_ko',''))
    return {'project':str(PROJECT),'qa_root':str(QA_ROOT),'run_id':run_id,'code':code,'card_id':code,'item_id':code,'category':card.get('category',{}),'source_file':None,'translation_file':None,'source_text':source,'current_ko':current_ko,'polished_ko':card.get('polished_ko'),'input_card':card,'facts':facts,'policy':POLICY.copy(),'agent_trace':[],'agent_results':{},'memory_updates_applied':False,'auto_apply':False}

def card_category_from_path(orig: Path) -> tuple[str, dict[str, str]]:
    parent = orig.parent.name
    if parent == 'Peaceful Encounter':
        return f'Peaceful Encounter/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'peaceful_encounter','text_type':'encounter_choice_text'}
    if parent == 'Delve':
        return f'Delve/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'delve','text_type':'delve_tile_text'}
    return f'Conflict Encounter/{orig.name.split(" - ")[0]}', {'component_type':'card','card_type':'conflict_encounter','text_type':'encounter_choice_text'}

def new_context(code: str, run_id: str) -> dict[str, Any]:
    orig, trans=find_card_files(code)
    source_text=orig.read_text(encoding='utf-8')
    current_ko=trans.read_text(encoding='utf-8')
    item_id, category = card_category_from_path(orig)
    if code in CARD_FACTS:
        facts=CARD_FACTS[code]
    else:
        facts=infer_generic_facts({'card_id': code, 'category': category, 'source_text': source_text, 'current_ko': current_ko, 'term_glossary': read_jsonl(QA_ROOT/'memory/term_db.jsonl'), 'syntax_dictionary': read_jsonl(QA_ROOT/'memory/syntax_rules.jsonl'), 'prior_translations': [], 'approved_qa_logs': []})
    return {'project':str(PROJECT),'qa_root':str(QA_ROOT),'run_id':run_id,'code':code,'card_id':code,'item_id':item_id,'category':category,'source_file':str(orig),'translation_file':str(trans),'source_text':source_text,'current_ko':current_ko,'facts':facts,'policy':POLICY.copy(),'agent_trace':[],'agent_results':{},'memory_updates_applied':False,'auto_apply':False}

def record_agent(context: dict[str, Any], agent_name: str, result: dict[str, Any]) -> dict[str, Any]:
    result=dict(result); result.setdefault('status','done')
    context.setdefault('agent_results',{})[agent_name]=result
    context.setdefault('agent_trace',[]).append({'agent_name':agent_name,'status':result['status'],'summary':result.get('summary','')})
    return context

def _count_slots(value: Any) -> int:
    if isinstance(value, dict):
        return sum(_count_slots(v) for v in value.values()) or len(value)
    if isinstance(value, list):
        return sum(_count_slots(v) for v in value) or len(value)
    return 1 if value not in [None, ''] else 0

def heuristic_parser_probe(source_text: str, ko_text: str) -> dict[str, Any]:
    """Lightweight generic parser probe used to measure seed dependence.

    This is not the authoritative QA result. It records what the generic parser can
    see without card-specific seed facts, so the harness can honestly report where
    it is still relying on seeded slots.
    """
    patterns = {
        'unstable': bool(re.search(r'ICON_Unstable_|\bUnstable:', source_text)),
        'persistent': 'Persistent:' in source_text,
        'three_plus_players': '3+ players' in source_text,
        'each_adventurer': 'Each adventurer' in source_text,
        'battle_objective': 'battleObjective' in source_text,
        'choice_type': 'choiceType' in source_text,
        'timing_after_each': bool(re.search(r'After each time|Each time|At the start|At the end', source_text)),
        'scope_each_hex': 'each hex' in source_text.lower(),
    }
    extracted = [k for k, v in patterns.items() if v]
    ko_markers = {
        'unstable_icon_ko': 'ICON_Unstable' in ko_text or '불안정' in ko_text,
        'persistent_ko': '지속:' in ko_text,
        'three_plus_ko': '3+ 인' in ko_text or '3 이상' in ko_text,
        'each_adventurer_ko': '각 모험가' in ko_text,
        'battle_objective_ko': '전투 목표' in ko_text,
    }
    return {'extracted_markers': extracted, 'marker_count': len(extracted), 'ko_markers': ko_markers}

def build_step_quality(context: dict[str, Any]) -> dict[str, Any]:
    facts = context.get('facts', {})
    probe = heuristic_parser_probe(context.get('source_text', ''), context.get('current_ko', ''))
    seeded_slots = _count_slots(facts.get('source_slots', {}))
    marker_count = probe['marker_count']
    return {
        'seed_vs_parser': {
            'seeded_slot_count': seeded_slots,
            'generic_marker_count': marker_count,
            'seed_dependency': 'high' if seeded_slots > marker_count + 3 else 'medium' if seeded_slots > marker_count else 'low',
            'generic_parser_probe': probe,
        }
    }

def pipeline_steps(context: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    steps = {f'{i:02d}_{name}': {'status': 'done', 'evidence': 'agent pipeline artifact에 결과 기록'} for i, name in enumerate(PIPELINE_NAMES, 1)}
    steps['22_사람 승인 후 memory 업데이트'] = {'status': 'skipped_pending_human_approval', 'evidence': '본 pass는 suggestion_only; memory 확정 업데이트 금지'}
    if not context:
        return steps
    issues = context.get('issues') or context.get('facts', {}).get('issues', [])
    has_major = any(i.get('severity') in ['Critical', 'Major'] for i in issues)
    has_note = any(i.get('severity') in ['Note', 'Minor', 'Info'] for i in issues)
    if context.get('ontology_result', {}).get('status') == 'not_available':
        steps['07_lore ontology 검사'] = {'status': 'not_available', 'evidence': 'ontology DB/hits 없음; 검증 대신 가용성 기록'}
    if context.get('patch_result', {}).get('status') == 'not_available':
        steps['08_patch note 검사'] = {'status': 'not_available', 'evidence': 'patch/improvement notes 없음; 검증 대신 가용성 기록'}
    elif context.get('patch_result', {}).get('status') == 'not_applicable':
        steps['08_patch note 검사'] = {'status': 'skipped_not_applicable', 'evidence': 'patch/improvement notes는 있으나 이 source에는 적용되지 않음'}
    elif context.get('patch_result', {}).get('status') == 'warn':
        steps['08_patch note 검사'] = {'status': 'warn', 'evidence': '적용 가능한 patch/improvement 중 current_ko 반영 여부 확인 필요'}
    elif context.get('patch_result', {}).get('status') == 'pass':
        steps['08_patch note 검사'] = {'status': 'done', 'evidence': '적용 가능한 patch/improvement가 current_ko에 반영됨'}
    if context.get('source_analysis', {}).get('semantic_pattern') == 'UNRESOLVED':
        steps['03_원문 의미 패턴 추출'] = {'status': 'block', 'evidence': 'semantic pattern unresolved'}
        steps['04_원문 슬롯 추출'] = {'status': 'block', 'evidence': 'source slots unresolved'}
    if has_major:
        steps['15_룰 리스크 평가'] = {'status': 'block', 'evidence': 'Major/Critical issue blocks approval'}
        steps['18_최종 QA 판정'] = {'status': 'block', 'evidence': context.get('verdict', 'Needs revision')}
    elif has_note:
        steps['15_룰 리스크 평가'] = {'status': 'warn', 'evidence': 'Note/Minor issue only'}
        steps['18_최종 QA 판정'] = {'status': 'warn', 'evidence': context.get('verdict', 'Suggestion')}
    if context.get('learning_update_proposal'):
        steps['21_학습 반영 제안 생성'] = {'status': 'warn' if not has_major else 'block', 'evidence': 'learning/proposal candidates generated; human approval required'}
    sv = context.get('self_verification') or {}
    if sv:
        failed_passes = [k for k, v in sv.items() if k.endswith('_pass') and v is False]
        if not sv.get('blocking_issue_pass', True):
            steps['17_수정안 self-verification'] = {'status': 'block', 'evidence': f"blocking issues remain: {', '.join(sv.get('blocking_issue_ids') or [])}"}
        elif failed_passes or not sv.get('meaning_preserved', True):
            steps['17_수정안 self-verification'] = {'status': 'warn', 'evidence': f"self-verification warning: {', '.join(failed_passes) or 'meaning_preserved'}"}
        else:
            steps['17_수정안 self-verification'] = {'status': 'done', 'evidence': 'self-verification gates passed'}
    return steps

def now_iso() -> str: return datetime.datetime.now().isoformat()
