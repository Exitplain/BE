"""Auditable hypothesis model. These coefficients are not calibrated to people."""
STAGES={'/':'landing','/product':'product','/cart':'cart','/signup':'signup',
        '/checkout':'checkout','/order/success':'complete','/delivery':'delivery',
        '/delivery/result':'complete','/benefits':'benefits','/coupon/success':'coupon',
        '/account':'account','/subscription':'subscription','/subscription/skipped':'complete'}
PARAMETERS={'version':'frustration-v2','decay':0.85,'input_weight':16.0,
            'revisit_weight':8.0,'progress_reward':4.0,'calibration':'uncalibrated',
            'search_weight':10.0,'cost_weight':40.0,
            'decision':'deterministic_threshold','formula':'Fₜ = max(0, 0.85 Fₜ₋₁ + 16 c_input + 8 c_revisit + 10 s_search c_scroll + 40 s_cost c_cost − 4 r_progress)'}


class FrustrationMeter:
    def __init__(self,profile):
        self.threshold=profile['max_required_fields']*PARAMETERS['input_weight']
        self.score=0.0
        self.seen={}
        self.previous_stage=None
        self.cost_seen=False
        self.search_sensitivity=profile.get('search_sensitivity',1.0)
        self.cost_sensitivity=profile.get('cost_sensitivity',1.0)

    def observe(self,stage,required_fields,search_scroll=False,unexpected_cost=False):
        previous=self.score
        new_stage=stage not in self.seen
        revisit=bool(not new_stage and self.previous_stage!=stage)
        # Count newly encountered required controls, not each unfilled keystroke.
        added=max(0,required_fields-self.seen.get(stage,0))
        self.seen[stage]=max(required_fields,self.seen.get(stage,0))
        cost=bool(unexpected_cost and not self.cost_seen)
        if cost:self.cost_seen=True
        search_term=PARAMETERS['search_weight']*self.search_sensitivity*search_scroll
        cost_term=PARAMETERS['cost_weight']*self.cost_sensitivity*cost
        self.score=round(max(0,PARAMETERS['decay']*previous+PARAMETERS['input_weight']*added+
                             PARAMETERS['revisit_weight']*revisit+search_term+cost_term-PARAMETERS['progress_reward']*new_stage),3)
        self.previous_stage=stage
        return {'stage':stage,'score':self.score,'previous_score':previous,'threshold':self.threshold,
                'required_fields':required_fields,'signals':{'new_required_fields':added,'revisit':int(revisit),
                'new_stage':int(new_stage),'search_scroll':int(search_scroll),'unexpected_cost':int(cost)},'terms':{'retained':round(PARAMETERS['decay']*previous,3),
                'input':PARAMETERS['input_weight']*added,'revisit':PARAMETERS['revisit_weight']*revisit,
                'progress_reward':PARAMETERS['progress_reward']*new_stage,'search':search_term,'cost':cost_term},
                'crossed':self.score>self.threshold,'model_version':PARAMETERS['version']}
