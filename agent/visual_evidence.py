"""Screenshot annotations from observed DOM geometry, not inferred OCR locations."""

def annotation(observation,category):
    viewport=observation.get('viewport');regions=[]
    if category=='input_burden':
        regions=[{'box':e['box'],'label':e.get('label') or '필수 입력','kind':'input'}
                 for e in observation.get('elements',[]) if e.get('required') and e.get('box')]
    elif category=='cost':
        regions=[{'box':b,'label':'늦게 표시된 배송비','kind':'cost'} for b in observation.get('cost_warning_boxes',[])]
    return {'viewport':viewport,'regions':regions,'source':'observed_dom',
            'note':'관측 시점의 보이는 요소 좌표입니다.' if regions else
                   '화면 안에서 특정 문제 요소의 좌표를 확보하지 못했습니다. 화면 밖 버튼 위치를 추정해 표시하지 않습니다.'}
