"""Task contracts and visible-label fixtures; policies never read variant/test answers."""
from urllib.parse import urlsplit
from .policies import ShopScriptedPolicy
from .schemas import Action

TASKS={
 'order':{'id':'order','name':'첫 주문','goal':'반찬 한 상을 골라 첫 모의 주문 완료','case':'signup','success_path':'/order/success'},
 'delivery':{'id':'delivery','name':'배송 지역 확인','goal':'서울 노원구의 배송 가능 여부 확인','case':'delivery','success_path':'/delivery/result'},
 'coupon':{'id':'coupon','name':'쿠폰으로 주문','goal':'첫 주문 쿠폰을 받고 적용해 모의 주문 완료','case':'coupon','success_path':'/order/success'},
 'subscription':{'id':'subscription','name':'다음 배송 건너뛰기','goal':'모의 구독에서 다음 배송 한 번 건너뛰기','case':'subscription','success_path':'/subscription/skipped'},
 'cart':{'id':'cart','name':'장바구니 담기','goal':'반찬을 골라 장바구니에 담긴 내용 확인','case':'cta','success_path':'/cart'},
 'benefit':{'id':'benefit','name':'쿠폰 받기','goal':'첫 주문 할인 쿠폰 발급 완료','case':'coupon','success_path':'/coupon/success'},
}


DEFAULT_TASK_IDS=('order','delivery','coupon','subscription')

def task_succeeded(task,page_path,text):
    if page_path!=task['success_path']:return False
    if task['id']=='coupon':return '쿠폰 적용 완료' in text
    if task['id']=='cart':return '아직 식탁이 비어' not in text and '수량' in text
    return True


class ShopTaskPolicy:
    def __init__(self,task_id):self.task_id=task_id;self.order=ShopScriptedPolicy()

    async def decide(self,observation,persona,goal,history):
        elements=observation['elements'];path=urlsplit(observation['url']).path
        targets=[]
        if self.task_id=='delivery':targets=['배송 가능 지역 확인'] if path=='/' else ['배송 가능 여부 확인']
        elif self.task_id=='subscription':
            targets=['내 구독 관리'] if path=='/' else ['배송 일정 관리'] if path=='/account' else ['다음 배송 건너뛰기','건너뛰기 확정']
        elif self.task_id in {'coupon','benefit'}:
            if path=='/':targets=['첫 주문 혜택']
            elif path=='/benefits':targets=['첫 주문 쿠폰 받기']
            elif path=='/coupon/success':targets=['쿠폰으로 주문하기']
        if targets:
            # Fill only visible synthetic task inputs; no customer account data.
            for element in elements:
                if element['tag'] in {'input','textarea'} and element['type'] not in {'checkbox','radio','submit'} and not element['value']:
                    values={'배송 지역':'서울 노원구','휴대폰 번호':'01000000000','이메일':'fixture@example.test'}
                    return Action(kind='fill',target_id=element['id'],value=values.get(element.get('label'),'모의 테스트'),explanation='과업의 모의 정보 입력'),{}
                if element['type']=='checkbox' and not element.get('checked'):
                    return Action(kind='check',target_id=element['id'],value=None,explanation='모의 과업 안내 확인'),{}
            for name in targets:
                target=next((e for e in elements if e['text'].strip().startswith(name)),None)
                if target:return Action(kind='click',target_id=target['id'],value=None,explanation=name+' 선택'),{}
            return Action(kind='scroll',target_id=None,value='down',explanation='과업의 다음 동작을 화면에서 탐색'),{}
        return await self.order.decide(observation,persona,goal,history)
