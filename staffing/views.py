# coding: utf-8
"""
Pydici staffing views. Http request are processed here.
@author: Sébastien Renard (sebastien.renard@digitalfox.org)
@license: GPL v3 or newer
"""

from datetime import date, timedelta, datetime

from django.shortcuts import render_to_response
from django.http import HttpResponseRedirect, HttpResponse
from django.contrib.auth.decorators import permission_required
from django.forms.models import inlineformset_factory
from django.utils.translation import ugettext as _
from django.core import urlresolvers
from django.template import RequestContext

from pydici.staffing.models import Staffing, Mission, Holiday
from pydici.people.models import Consultant
from pydici.staffing.forms import ConsultantStaffingInlineFormset, MissionStaffingInlineFormset
from pydici.core.utils import working_days, to_int_or_round

def missions(request, onlyActive=True):
    """List of missions"""
    if onlyActive:
        missions = Mission.objects.filter(active=True)
        all = False
    else:
        missions = Mission.objects.all()
        all = True
    return render_to_response("leads/missions.html",
                              {"missions": missions,
                               "all": all,
                               "user": request.user },
                               RequestContext(request))


@permission_required("leads.add_staffing")
@permission_required("leads.change_staffing")
@permission_required("leads.delete_staffing")
def mission_staffing(request, mission_id):
    """Edit mission staffing"""
    StaffingFormSet = inlineformset_factory(Mission, Staffing,
                                            formset=MissionStaffingInlineFormset)
    mission = Mission.objects.get(id=mission_id)
    if request.method == "POST":
        formset = StaffingFormSet(request.POST, instance=mission)
        if formset.is_valid():
            saveFormsetAndLog(formset, request)
            formset = StaffingFormSet(instance=mission) # Recreate a new form for next update
    else:
        formset = StaffingFormSet(instance=mission) # An unbound form

    consultants = set([s.consultant for s in mission.staffing_set.all()])
    consultants = list(consultants)
    consultants.sort(cmp=lambda x, y: cmp(x.name, y.name))
    return render_to_response('leads/mission_staffing.html',
                              {"formset": formset,
                               "mission": mission,
                               "consultants": consultants,
                               "user": request.user},
                               RequestContext(request))



def consultant_staffing(request, consultant_id):
    """Edit consultant staffing"""
    consultant = Consultant.objects.get(id=consultant_id)

    if not (request.user.has_perm("leads.add_staffing") and
            request.user.has_perm("leads.change_staffing") and
            request.user.has_perm("leads.delete_staffing")):
        # Only forbid access if the user try to edit someone else staffing
        if request.user.username.upper() != consultant.trigramme:
            return HttpResponseRedirect(urlresolvers.reverse("forbiden"))

    StaffingFormSet = inlineformset_factory(Consultant, Staffing,
                                          formset=ConsultantStaffingInlineFormset)

    if request.method == "POST":
        formset = StaffingFormSet(request.POST, instance=consultant)
        if formset.is_valid():
            saveFormsetAndLog(formset, request)
            formset = StaffingFormSet(instance=consultant) # Recreate a new form for next update
    else:
        formset = StaffingFormSet(instance=consultant) # An unbound form

    missions = set([s.mission for s in consultant.staffing_set.all() if s.mission.active])
    missions = list(missions)
    missions.sort(cmp=lambda x, y: cmp(x.lead, y.lead))

    return render_to_response('leads/consultant_staffing.html',
                              {"formset": formset,
                               "consultant": consultant,
                               "missions": missions,
                               "user": request.user },
                               RequestContext(request))


def pdc_review(request, year=None, month=None):
    """PDC overview
    @param year: start date year. None means current year
    @param year: start date year. None means curre    nt month"""

    # Don't display this page if no productive consultant are defined
    if Consultant.objects.filter(productive=True).count() == 0:
        #TODO: make this message nice
        return HttpResponse(_("No productive consultant defined !"))

    n_month = 3
    if "n_month" in request.GET:
        try:
            n_month = int(request.GET["n_month"])
            if n_month > 12:
                n_month = 12 # Limit to 12 month to avoid complex and useless month list computation
        except ValueError:
            pass

    if "projected" in request.GET:
        projected = True
    else:
        projected = False

    groupby = "manager"
    if "groupby" in request.GET:
        if request.GET["groupby"] in ("manager", "position"):
            groupby = request.GET["groupby"]

    if year and month:
        start_date = date(int(year), int(month), 1)
    else:
        start_date = date.today()
        start_date = start_date.replace(day=1) # We use the first day to represent month

    staffing = {} # staffing data per month and per consultant
    total = {}    # total staffing data per month
    rates = []     # staffing rates per month
    available_month = {} # available working days per month
    months = []   # list of month to be displayed
    people = Consultant.objects.filter(productive=True).count()

    for i in range(n_month):
        if start_date.month + i <= 12:
            months.append(start_date.replace(month=start_date.month + i))
        else:
            # We wrap around a year (max one year)
            months.append(start_date.replace(month=start_date.month + i - 12, year=start_date.year + 1))

    previous_slice_date = start_date - timedelta(days=31 * n_month)
    next_slice_date = start_date + timedelta(days=31 * n_month)

    # Initialize total dict and available dict
    holidays_days = [h.day for h in Holiday.objects.all()]
    for month in months:
        total[month] = {"prod":0, "unprod":0, "holidays":0, "available":0}
        available_month[month] = working_days(month, holidays_days)

    # Get consultants staffing
    for consultant in Consultant.objects.select_related().filter(productive=True):
        staffing[consultant] = []
        missions = set()
        for month in months:
            if projected:
                current_staffings = consultant.staffing_set.filter(staffing_date=month).order_by()
            else:
                # Only keep 100% mission
                current_staffings = consultant.staffing_set.filter(staffing_date=month, mission__probability=100).order_by()

            # Sum staffing
            prod = []
            unprod = []
            holidays = []
            for current_staffing  in current_staffings:
                nature = current_staffing.mission.nature
                if nature == "PROD":
                    missions.add(current_staffing.mission) # Store prod missions for this consultant
                    prod.append(current_staffing.charge * current_staffing.mission.probability / 100)
                elif nature == "NONPROD":
                    unprod.append(current_staffing.charge * current_staffing.mission.probability / 100)
                elif nature == "HOLIDAYS":
                    holidays.append(current_staffing.charge * current_staffing.mission.probability / 100)

            # Staffing computation
            prod = to_int_or_round(sum(prod))
            unprod = to_int_or_round(sum(unprod))
            holidays = to_int_or_round(sum(holidays))
            available = available_month[month] - (prod + unprod + holidays)
            staffing[consultant].append([prod, unprod, holidays, available])
            total[month]["prod"] += prod
            total[month]["unprod"] += unprod
            total[month]["holidays"] += holidays
            total[month]["available"] += available
        # Add mission synthesis to staffing dict
        staffing[consultant].append([", ".join(["<a href='%s'>%s</a>" %
                                        (urlresolvers.reverse("pydici.staffing.views.mission_staffing", args=[m.id]),
                                        m.short_name()) for m in list(missions)])])

    # Compute indicator rates
    for month in months:
        rate = []
        ndays = people * available_month[month] # Total days for this month
        for indicator in ("prod", "unprod", "holidays", "available"):
            if indicator == "holidays":
                rate.append(100.0 * total[month][indicator] / ndays)
            else:
                rate.append(100.0 * total[month][indicator] / (ndays - total[month]["holidays"]))
        rates.append(map(lambda x: to_int_or_round(x), rate))

    # Format total dict into list
    total = total.items()
    total.sort(cmp=lambda x, y:cmp(x[0], y[0])) # Sort according date
    # Remove date, and transform dict into ordered list:
    total = [(to_int_or_round(i[1]["prod"]),
            to_int_or_round(i[1]["unprod"]),
            to_int_or_round(i[1]["holidays"]),
            to_int_or_round(i[1]["available"])) for i in total]

    # Order staffing list
    staffing = staffing.items()
    staffing.sort(cmp=lambda x, y:cmp(x[0].name, y[0].name)) # Sort by name
    if groupby == "manager":
        staffing.sort(cmp=lambda x, y:cmp(unicode(x[0].manager), unicode(y[0].manager))) # Sort by manager
    else:
        staffing.sort(cmp=lambda x, y:cmp(x[0].profil.level, y[0].profil.level)) # Sort by position

    return render_to_response("leads/pdc_review.html",
                              {"staffing": staffing,
                               "months": months,
                               "total": total,
                               "rates": rates,
                               "user": request.user,
                               "projected": projected,
                               "previous_slice_date" : previous_slice_date,
                               "next_slice_date" : next_slice_date,
                               "start_date" : start_date,
                               "groupby" : groupby},
                               RequestContext(request))

def deactivate_mission(request, mission_id):
    """Deactivate the given mission"""
    mission = Mission.objects.get(id=mission_id)
    mission.active = False
    mission.save()
    return HttpResponseRedirect(urlresolvers.reverse("missions"))


def saveFormsetAndLog(formset, request):
    """Save the given staffing formset and log last user"""
    now = datetime.now()
    now = now.replace(microsecond=0) # Remove useless microsecond that pollute form validation in callback
    staffings = formset.save(commit=False)
    for staffing in staffings:
        staffing.last_user = unicode(request.user)
        staffing.update_date = now
        staffing.save()