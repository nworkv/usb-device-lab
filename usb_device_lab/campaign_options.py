"""Validated in-memory timing overrides for the next operator campaign."""
from dataclasses import replace
import math

FIELDS = ('iterations', 'seconds', 'pre_run_quiet_seconds', 'post_run_capture_seconds')


def validate_options(raw):
    if type(raw) is not dict or set(raw) != set(FIELDS):
        raise ValueError('Укажите число попыток, длительность и оба quiet-окна')
    iterations = raw['iterations']
    if type(iterations) is not int or not 1 <= iterations <= 10000:
        raise ValueError('iterations: целое число 1..10000')
    values = {'iterations': iterations}
    for field in FIELDS[1:]:
        value = raw[field]
        maximum = 300 if field == 'seconds' else 30
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum or field == 'seconds' and value == 0:
            raise ValueError(field + ': число вне допустимого диапазона')
        values[field] = float(value)
    return values


def option_values(config, overrides):
    values = {field: getattr(config, field) for field in FIELDS}
    values.update(overrides)
    return values


def apply_options(config, overrides):
    return replace(config, **validate_options(overrides)) if overrides else config


def window_budget(values):
    return values['iterations'] * (values['seconds'] + values['pre_run_quiet_seconds'] + values['post_run_capture_seconds'])


SCRIPT = """'use strict';
const campaignOptionFields=['iterations','seconds','pre_run_quiet_seconds','post_run_capture_seconds'];
const campaignOptionLabels=['Число попыток','USB-окно, с','Тишина до запуска, с','Захват после запуска, с'];
const campaignOptionPanel=node('article','','glass'),campaignOptionInputs=new Map();
campaignOptionPanel.append(node('h2','Параметры следующей кампании'));
campaignOptionFields.forEach((field,i)=>{const label=node('label',campaignOptionLabels[i]+' '),input=document.createElement('input');input.type='number';input.step=field==='iterations'?'1':'any';input.min=field==='iterations'?'1':'0';input.max=field==='iterations'?'10000':field==='seconds'?'300':'30';input.setAttribute('aria-label',campaignOptionLabels[i]);label.append(input);campaignOptionPanel.append(label);campaignOptionInputs.set(field,input);});
const campaignOptionApply=node('button','Применить параметры'),campaignOptionFast=node('button','Короткие окна: 0,2 / 0,1 / 0,2 с'),campaignOptionBudget=node('p','','muted');
campaignOptionPanel.append(campaignOptionApply,campaignOptionFast,campaignOptionBudget,node('p','Настройки действуют в памяти панели, без изменения TOML. Короткие окна могут пропустить enumeration и поздние сообщения. Повтор сохранённой попытки использует свою длительность.','muted'));el('run').append(campaignOptionPanel);
let campaignOptionEpoch=-1,campaignOptionDirty=false;
for(const input of campaignOptionInputs.values())input.oninput=()=>{campaignOptionDirty=true;};
campaignOptionFast.onclick=()=>{campaignOptionInputs.get('seconds').value='0.2';campaignOptionInputs.get('pre_run_quiet_seconds').value='0.1';campaignOptionInputs.get('post_run_capture_seconds').value='0.2';campaignOptionDirty=true;};
campaignOptionApply.onclick=async()=>{if(!token)return;const values=Object.fromEntries(campaignOptionFields.map(field=>[field,Number(campaignOptionInputs.get(field).value)]));try{state=await api('/api/options',values);campaignOptionDirty=false;await refresh();}catch(error){el('error').textContent=error.message;}};
setInterval(()=>{
 if(campaignOptionEpoch!==epoch){campaignOptionEpoch=epoch;campaignOptionDirty=false;for(const input of campaignOptionInputs.values())input.value='';}
 const blocked=!token||!state||Boolean(state.busy);campaignOptionApply.disabled=campaignOptionFast.disabled=blocked;for(const input of campaignOptionInputs.values())input.disabled=blocked;
 if(!token||!state?.campaign_options){campaignOptionBudget.textContent='';return;}
 if(!campaignOptionDirty)for(const field of campaignOptionFields)campaignOptionInputs.get(field).value=String(state.campaign_options[field]);
 const values=campaignOptionFields.map(field=>Number(campaignOptionInputs.get(field).value));
 const seconds=values[0]*(values[1]+values[2]+values[3]);campaignOptionBudget.textContent=Number.isFinite(seconds)?'Сумма заданных окон: '+seconds.toFixed(1)+' с, без подключения, завершения процессов и анализа. Не прогноз полного времени.':'';
},250);
""".encode('utf-8')
