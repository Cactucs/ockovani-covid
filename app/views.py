from datetime import timedelta, date

from flask import render_template
from sqlalchemy import func, text, or_
from werkzeug.exceptions import abort

from app import db, bp
from app.models import Import, Okres, Kraj, OckovaciMisto, VolnaMistaCas, VolnaMistaDen

STATUS_FINISHED = 'FINISHED'


@bp.route('/')
def index():
    return render_template('index.html', last_update=last_update())


@bp.route("/okres/<okres_nazev>")
def info_okres(okres_nazev):
    okres = db.session.query(Okres).filter(Okres.nazev == okres_nazev).one_or_none()
    if okres is None:
        abort(404)

    nactene_informace = db.session.query(Okres.nazev.label("okres"), Kraj.nazev.label("kraj"), OckovaciMisto.nazev,
                                         VolnaMistaDen.datum, VolnaMistaDen.volna_mista.label("pocet_mist"),
                                         OckovaciMisto.id) \
        .join(VolnaMistaDen, (VolnaMistaDen.misto_id == OckovaciMisto.id)) \
        .outerjoin(Okres, (OckovaciMisto.okres_id == Okres.id)) \
        .outerjoin(Kraj, (Okres.kraj_id == Kraj.id)) \
        .filter(Okres.nazev == okres_nazev) \
        .filter(VolnaMistaDen.import_id == last_update_import_id()) \
        .filter(OckovaciMisto.status == True) \
        .order_by(VolnaMistaDen.datum, OckovaciMisto.nazev) \
        .all()

    return render_template('okres.html', data=nactene_informace, okres=okres, last_update=last_update())


@bp.route("/kraj/<kraj_name>")
def info_kraj(kraj_name):
    kraj = db.session.query(Kraj).filter(Kraj.nazev == kraj_name).one_or_none()
    if kraj is None:
        abort(404)

    nactene_informace = db.session.query(Okres.nazev.label("okres"), Kraj.nazev.label("kraj"), OckovaciMisto.nazev,
                                         VolnaMistaDen.datum, VolnaMistaDen.volna_mista.label("pocet_mist"),
                                         OckovaciMisto.id) \
        .join(VolnaMistaDen, (VolnaMistaDen.misto_id == OckovaciMisto.id)) \
        .outerjoin(Okres, (OckovaciMisto.okres_id == Okres.id)) \
        .outerjoin(Kraj, (Okres.kraj_id == Kraj.id)) \
        .filter(Kraj.nazev == kraj_name) \
        .filter(VolnaMistaDen.import_id == last_update_import_id()) \
        .filter(OckovaciMisto.status == True) \
        .order_by(VolnaMistaDen.datum, OckovaciMisto.okres, OckovaciMisto.nazev) \
        .all()

    return render_template('kraj.html', data=nactene_informace, kraj=kraj, last_update=last_update())


@bp.route("/misto/<misto_id>")
def info_misto(misto_id):
    misto = db.session.query(OckovaciMisto).filter(OckovaciMisto.id == misto_id).one_or_none()
    if misto is None:
        abort(404)

    nactene_informace = db.session.query(Okres.nazev.label("okres"), Kraj.nazev.label("kraj"), OckovaciMisto.nazev,
                                         VolnaMistaDen.datum, VolnaMistaDen.volna_mista.label("pocet_mist"),
                                         OckovaciMisto.id, OckovaciMisto.adresa,
                                         OckovaciMisto.latitude, OckovaciMisto.longitude,
                                         OckovaciMisto.minimalni_kapacita,
                                         OckovaciMisto.bezbarierovy_pristup) \
        .join(VolnaMistaDen, (VolnaMistaDen.misto_id == OckovaciMisto.id)) \
        .outerjoin(Okres, (OckovaciMisto.okres_id == Okres.id)) \
        .outerjoin(Kraj, (Okres.kraj_id == Kraj.id)) \
        .filter(OckovaciMisto.id == misto.id) \
        .filter(VolnaMistaDen.import_id == last_update_import_id()) \
        .filter(OckovaciMisto.status == True) \
        .order_by(VolnaMistaDen.datum) \
        .all()

    ampule_info = db.session.query("vyrobce", "operace", "sum").from_statement(text(
        """
        select * from (select sum(pocet_ampulek) as sum,vyrobce,\'Příjem\' as operace from ockovani_distribuce 
        where ockovaci_misto_id=:misto_id and akce=\'Příjem\' group by vyrobce union 
        (select coalesce(sum(pocet_ampulek),0)as sum,vyrobce,\'Výdej\' as operace from ockovani_distribuce 
        where ockovaci_misto_id=:misto_id and akce=\'Výdej\' group by vyrobce) union 
        (select coalesce(sum(pocet_ampulek),0)as sum,vyrobce,\'Příjem odjinud\' as operace from ockovani_distribuce
        where cilove_ockovaci_misto_id=:misto_id and akce=\'Výdej\' group by vyrobce)
        union ( select sum(pouzite_ampulky), vyrobce, \'Očkováno\' as operace
        from ockovani_spotreba where ockovaci_misto_id=:misto_id group by vyrobce)
        union (select sum(znehodnocene_ampulky), vyrobce, \'Zničeno\' as operace
        from ockovani_spotreba where ockovaci_misto_id=:misto_id group by vyrobce) ) as tbl
        order by vyrobce, operace
        """
    )).params(misto_id=misto_id).all()

    total = _compute_vaccination_stats(ampule_info)

    return render_template('misto.html', data=nactene_informace, misto=misto, total=total, last_update=last_update())


def _compute_vaccination_stats(ampule_info):
    # Compute  statistics
    total = {
        'Pfizer': {
            'Přijato': {'ampule': 0},
            'Vydáno': {'ampule': 0},
            'Očkováno': {'ampule': 0},
            'Zničeno': {'ampule': 0},
            'Skladem': {'ampule': 0},
        },
        'Moderna': {
            'Přijato': {'ampule': 0},
            'Vydáno': {'ampule': 0},
            'Očkováno': {'ampule': 0},
            'Zničeno': {'ampule': 0},
            'Skladem': {'ampule': 0},
        },
        'AstraZeneca': {
            'Přijato': {'ampule': 0},
            'Vydáno': {'ampule': 0},
            'Očkováno': {'ampule': 0},
            'Zničeno': {'ampule': 0},
            'Skladem': {'ampule': 0},
        }
    }

    for item in ampule_info:
        operation = item[1]
        if operation in ('Příjem', 'Příjem odjinud'):
            total[item[0]]['Přijato']['ampule'] += item[2]
            total[item[0]]['Skladem']['ampule'] += item[2]
        elif operation == 'Výdej':
            total[item[0]]['Vydáno']['ampule'] += item[2]
            total[item[0]]['Skladem']['ampule'] -= item[2]
        elif operation == 'Očkováno':
            total[item[0]]['Očkováno']['ampule'] += item[2]
            total[item[0]]['Skladem']['ampule'] -= item[2]
        elif operation == 'Zničeno':
            total[item[0]]['Zničeno']['ampule'] += item[2]
            total[item[0]]['Skladem']['ampule'] -= item[2]

    total = _compute_vaccination_doses(total, 'Pfizer', 6)
    total = _compute_vaccination_doses(total, 'Moderna', 10)
    total = _compute_vaccination_doses(total, 'AstraZeneca', 10)

    total = _compute_vaccination_total(total)

    return total


def _compute_vaccination_doses(total, type, doses):
    for operation in total[type].keys():
        total[type][operation]['davky'] = total[type][operation]['ampule'] * doses
    return total


def _compute_vaccination_total(total):
    total_all = {
        'all': {
            'Přijato': {'ampule': 0, 'davky': 0},
            'Vydáno': {'ampule': 0, 'davky': 0},
            'Očkováno': {'ampule': 0, 'davky': 0},
            'Zničeno': {'ampule': 0, 'davky': 0},
            'Skladem': {'ampule': 0, 'davky': 0},
        }
    }

    for type in total.keys():
        for operation in total[type].keys():
            for dose in total[type][operation].keys():
                total_all['all'][operation][dose] += total[type][operation][dose]

    total.update(total_all)

    return total


@bp.route("/mista")
def info_mista():
    ockovani_info = db.session.query(OckovaciMisto.id, OckovaciMisto.nazev, OckovaciMisto.adresa,
                                     OckovaciMisto.latitude, OckovaciMisto.longitude,
                                     OckovaciMisto.minimalni_kapacita,
                                     OckovaciMisto.bezbarierovy_pristup,
                                     OckovaciMisto.service_id,
                                     OckovaciMisto.operation_id, OckovaciMisto.odkaz,
                                     Okres.nazev.label("okres"), Kraj.nazev.label("kraj"),
                                     func.sum(VolnaMistaDen.volna_mista).label("pocet_mist")) \
        .join(VolnaMistaDen, (VolnaMistaDen.misto_id == OckovaciMisto.id)) \
        .outerjoin(Okres, (OckovaciMisto.okres_id == Okres.id)) \
        .outerjoin(Kraj, (Okres.kraj_id == Kraj.id)) \
        .filter(VolnaMistaDen.import_id == last_update_import_id()) \
        .filter(OckovaciMisto.status == True) \
        .group_by(OckovaciMisto.id, OckovaciMisto.nazev, OckovaciMisto.adresa, OckovaciMisto.latitude,
                  OckovaciMisto.longitude, OckovaciMisto.minimalni_kapacita, OckovaciMisto.bezbarierovy_pristup,
                  OckovaciMisto.service_id, OckovaciMisto.operation_id, OckovaciMisto.odkaz,
                  Okres.nazev, Kraj.nazev) \
        .order_by(Kraj.nazev, Okres.nazev, OckovaciMisto.nazev) \
        .all()

    return render_template('mista.html', ockovaci_mista=ockovani_info, last_update=last_update())


@bp.route("/mapa")
def mapa():
    # todo: centers with some older imports (stopped working and were removed) will not fulfill the condition (not a problem now)
    ockovani_info = db.session.query(OckovaciMisto.id, OckovaciMisto.nazev, OckovaciMisto.adresa,
                                     OckovaciMisto.latitude, OckovaciMisto.longitude,
                                     OckovaciMisto.minimalni_kapacita,
                                     OckovaciMisto.bezbarierovy_pristup,
                                     OckovaciMisto.service_id,
                                     OckovaciMisto.operation_id, OckovaciMisto.odkaz,
                                     Okres.nazev.label("okres"), Kraj.nazev.label("kraj"),
                                     func.sum(VolnaMistaDen.volna_mista).label("pocet_mist")) \
        .outerjoin(VolnaMistaDen, (VolnaMistaDen.misto_id == OckovaciMisto.id)) \
        .outerjoin(Okres, (OckovaciMisto.okres_id == Okres.id)) \
        .outerjoin(Kraj, (Okres.kraj_id == Kraj.id)) \
        .filter(or_(VolnaMistaDen.import_id == last_update_import_id(), VolnaMistaDen.import_id == None)) \
        .filter(OckovaciMisto.status == True) \
        .group_by(OckovaciMisto.id, OckovaciMisto.nazev, OckovaciMisto.adresa, OckovaciMisto.latitude,
                  OckovaciMisto.longitude, OckovaciMisto.minimalni_kapacita, OckovaciMisto.bezbarierovy_pristup,
                  OckovaciMisto.service_id, OckovaciMisto.operation_id, OckovaciMisto.odkaz,
                  Okres.nazev, Kraj.nazev) \
        .order_by(Kraj.nazev, Okres.nazev, OckovaciMisto.nazev) \
        .all()

    return render_template('mapa.html', ockovaci_mista=ockovani_info, last_update=last_update())


@bp.route("/opendata")
def opendata():
    return render_template('opendata.html', last_update=last_update())


@bp.route("/statistiky")
def statistiky():
    vacc_storage = db.session.query("vyrobce", "prijem", "spotreba", "rozdil").from_statement(text(
        """
        select spotrebovane.vyrobce, prijem, spotreba, prijem-spotreba as rozdil  from (
        select vyrobce, sum(CASE WHEN vyrobce='Pfizer' then 6*pocet_ampulek ELSE 10*pocet_ampulek end) as prijem 
            from ockovani_distribuce where akce='Příjem' group by vyrobce) as prijate
            JOIN (
        select vyrobce, sum(CASE WHEN vyrobce='Pfizer' then 6*pouzite_ampulky ELSE 10*pouzite_ampulky end) as spotreba 
            from ockovani_spotreba group by vyrobce) as spotrebovane 
            on (prijate.vyrobce=spotrebovane.vyrobce)
        """
    )).all()

    top5_vaccination_day = db.session.query("datum", "sum").from_statement(text(
        """
        select datum, sum(pocet) from ockovani_lide 
        group by datum order by sum(pocet) desc limit 5
        """
    )).all()

    top5_vaccination_place_day = db.session.query("datum", "zarizeni_nazev", "sum").from_statement(text(
        """
        select datum, zarizeni_nazev, sum(pocet) from ockovani_lide 
        group by datum, zarizeni_nazev order by sum(pocet) desc limit 5;
        """
    )).all()

    vaccination_stats = db.session.query("celkem", "prvni", "druha").from_statement(text(
        """
        select sum(pocet) celkem, sum(case when poradi_davky=1 then pocet else 0 end) prvni,
        sum(case when poradi_davky='2' then pocet else 0 end) druha from ockovani_lide
        """
    )).all()
    cr_people = 8670000
    cr_factor = 0.6
    cr_to_vacc = cr_people * cr_factor
    delka_dny = (cr_to_vacc - vaccination_stats[0].celkem) * 2 / top5_vaccination_day[0].sum
    end_date = date.today() + timedelta(days=delka_dny)

    vaccination_age = db.session.query("vekova_skupina", "sum", "sum_2").from_statement(text(
        """
        select vekova_skupina, sum(pocet), sum(case when poradi_davky=2 then pocet else 0 end) sum_2 from ockovani_lide 
        group by vekova_skupina order by vekova_skupina;
        """
    )).all()

    # .params(misto_id=misto_id)

    return render_template('statistiky.html', last_update=last_update(), vacc_storage=vacc_storage, end_date=end_date,
                           top5=top5_vaccination_day,
                           top5_place=top5_vaccination_place_day,
                           vac_stats=vaccination_stats, vac_age=vaccination_age)


def last_update():
    last_import = db.session.query(func.max(Import.start)).filter(Import.status == STATUS_FINISHED).first()[0]
    if last_import is None:
        last_import_datetime = 'nikdy'
    else:
        last_import_datetime = last_import.strftime('%a %d. %m. %Y %H:%M')

    return last_import_datetime


def last_update_import_id():
    """
    For better filtering.
    @return:
    """
    last_run = db.session.query(func.max(Import.id)).filter(Import.status == STATUS_FINISHED).first()[0]
    if last_run is None:
        max_import_id = -1
    else:
        max_import_id = last_run

    return max_import_id
