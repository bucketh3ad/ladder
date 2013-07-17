from django.views.generic import (
    DetailView, CreateView, UpdateView, FormView, DeleteView,
)
from django.contrib import messages
from django.shortcuts import redirect, get_object_or_404
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.core.urlresolvers import reverse, reverse_lazy

from authtools.views import LoginRequiredMixin

from exchange.models import (
    TicketOffer, TicketRequest, TicketMatch, PhoneNumber
)
from exchange.forms import (
    TicketOfferForm, TicketRequestForm, AcceptTicketOfferForm, PhoneNumberForm,
    VerifyPhoneNumberForm, SelectTicketRequestForm, SetPrimaryPhoneNumberForm,
)


class CreatePhoneNumberView(LoginRequiredMixin, CreateView):
    """
    Presents the user with a form to add a new phone number to their account.
    """
    model = PhoneNumber
    form_class = PhoneNumberForm

    def get_success_url(self):
        return reverse('verify_phone_number', kwargs={'pk': self.object.pk})

    def form_valid(self, form):
        form.instance.profile = self.request.user.ladder_profile
        self.object = phone_number = form.save()
        phone_number.send_sms()
        return redirect(self.get_success_url())

create_phone_number = CreatePhoneNumberView.as_view()


class VerifyPhoneNumberView(LoginRequiredMixin, UpdateView):
    template_name = 'exchange/verify_phone_number.html'
    model = PhoneNumber
    form_class = VerifyPhoneNumberForm
    success_url = reverse_lazy('dashboard')
    context_object_name = 'phone_number'

    def get_queryset(self):
        return self.request.user.ladder_profile.phone_numbers.is_verifiable()

    def form_valid(self, form):
        self.object = form.save()
        ladder_profile = self.request.user.ladder_profile
        if not ladder_profile.verified_phone_number:
            ladder_profile.verified_phone_number = self.object
            ladder_profile.save()
        messages.success(self.request, 'Your phone number has been verified')
        return redirect(self.get_success_url())

verify_phone_number = VerifyPhoneNumberView.as_view()


class SetPrimaryPhoneNumberView(LoginRequiredMixin, UpdateView):
    template_name = 'exchange/set_primary_phone_number.html'
    model = PhoneNumber
    form_class = SetPrimaryPhoneNumberForm
    success_url = reverse_lazy('dashboard')
    context_object_name = 'phone_number'

    def get_queryset(self):
        return self.request.user.ladder_profile.phone_numbers.is_verified()

    def form_valid(self, form):
        ladder_profile = self.request.user.ladder_profile
        ladder_profile.verify_phone_number = form.instance
        return super(SetPrimaryPhoneNumberView, self).form_valid(form)

set_primary_phone_number = SetPrimaryPhoneNumberView.as_view()


class DeletePhoneNumberView(LoginRequiredMixin, DeleteView):
    template_name = 'exchange/delete_phone_number.html'
    model = PhoneNumber
    success_url = reverse_lazy('dashboard')
    context_object_name = 'phone_number'

    def get_queryset(self):
        return self.request.user.ladder_profile.phone_numbers.all()

    def get_success_url(self):
        messages.success(self.request, 'Phone number deleted')
        return super(DeletePhoneNumberView, self).get_success_url()

delete_phone_number = DeletePhoneNumberView.as_view()


class CreateOfferView(LoginRequiredMixin, CreateView):
    template_name = 'exchange/offer_create.html'
    model = TicketOffer
    form_class = TicketOfferForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.ladder_profile.can_offer_ticket:
            raise PermissionDenied("User not eligable for ticket offer")
        return super(CreateOfferView, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        self.object = ticket_offer = form.save(commit=False)
        ticket_offer.user = self.request.user
        ticket_offer.save()
        if ticket_offer.is_automatch:
            # Possible race condition here.  Two offers are created at almost
            # the same time.  Two matches will be created.
            try:
                ticket_request = TicketRequest.objects.is_active().order_by('created_at')[0]
                TicketMatch.objects.create(
                    ticket_offer=ticket_offer,
                    ticket_request=ticket_request,
                )
                messages.success(self.request, "Your ticket offer has been matched with a ticket request.")
            except IndexError:
                messages.success(self.request, "Your ticket offer has been created and will be automatically matched to the next ticket request that enters the system")
            return redirect(self.get_success_url())
        return redirect(reverse('offer_select_recipient', kwargs={'pk': ticket_offer.pk}))

offer_create = CreateOfferView.as_view()


class OfferDetailView(LoginRequiredMixin, DetailView):
    template_name = 'exchange/listing_detail.html'
    model = TicketOffer
    context_object_name = 'ticket_offer'

    def get_queryset(self):
        return self.request.user.ticket_offers.all()

offer_detail = OfferDetailView.as_view()


class OfferSelectRecipientView(LoginRequiredMixin, FormView):
    """
    Presents the person offering their ticket with a choice of 3 ticket
    requests.
    """
    template_name = 'exchange/select_offer_recipient.html'
    form_class = SelectTicketRequestForm

    def get_ticket_offer(self):
        return get_object_or_404(
            self.request.user.ticket_offers.is_active(),
            pk=self.kwargs['pk'],
        )

    def get_ticket_request_queryset(self):
        return TicketRequest.objects.is_active().order_by('created_at')[:3]

    def get_form(self, form_class):
        form = super(OfferSelectRecipientView, self).get_form(form_class)
        form.fields['ticket_request'].queryset = self.get_ticket_request_queryset()
        return form

    def get_context_data(self, **kwargs):
        kwargs = super(OfferSelectRecipientView, self).get_context_data(**kwargs)
        kwargs['ticket_offer'] = self.get_ticket_offer()
        return kwargs

    def form_valid(self, form):
        ticket_offer = self.get_ticket_offer()
        ticket_request = form.cleaned_data['ticket_request']
        # Race Condition.  If two people select the same recipient at the same
        # time, two offers may be sent to to the requester.
        TicketMatch.objects.create(
            ticket_request=ticket_request,
            ticket_offer=ticket_offer,
        )
        messages.success(self.request, 'The ticket requester has been contacted.  Once they accept your ticket, we will put both of you in touch with each other')
        return redirect(ticket_request.get_absolute_url())

offer_select_recipient = OfferSelectRecipientView.as_view()


class RequestTicketView(LoginRequiredMixin, CreateView):
    template_name = 'exchange/request_create.html'
    model = TicketRequest
    form_class = TicketRequestForm

    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        if not request.user.ladder_profile.can_request_ticket:
            raise PermissionDenied("User not eligable for ticket reuqest.")
        return super(RequestTicketView, self).dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        ticket_request = form.save(commit=False)
        ticket_request.user = self.request.user
        ticket_request.save()
        # Possible race condition here.  Two requests are created at almost
        # the same time.  The same offer may be matched with two requests.
        try:
            ticket_offer = TicketOffer.objects.is_active().filter(is_automatch=True).order_by('created_at')[0]
            TicketMatch.objects.create(
                ticket_offer=ticket_offer,
                ticket_request=ticket_request,
            )
            messages.success(self.request, "Your request has been matched with a ticket.")
        except IndexError:
            messages.success(self.request, "Your request has been created.  You will be notified as soon as we find a ticket for you.")
        return redirect(ticket_request.get_absolute_url())

request_create = RequestTicketView.as_view()


class RequestTicketUpdateView(UpdateView):
    template_name = 'exchange/request_edit.html'
    model = TicketRequest
    context_object_name = 'ticket_request'

request_edit = RequestTicketUpdateView.as_view()


class RequestDetailView(DetailView):
    template_name = 'exchange/request_detail.html'
    model = TicketRequest
    context_object_name = 'ticket_request'

    def get_queryset(self):
        return self.request.user.ticket_requests.active()

request_detail = RequestDetailView.as_view()


class AcceptTicketOfferView(UpdateView):
    template_name = 'exchange/accept_ticket_offer.html'
    model = TicketMatch
    context_object_name = 'match'
    form_class = AcceptTicketOfferForm

    def form_valid(self, form):
        if '_reject' in self.request.POST:
            ticket_request = form.instance.ticket_request
            ticket_request.is_cancelled = True
            ticket_request.save()
            form.instance.is_terminated = True
            form.instance.save()
            messages.info(self.request, 'Your ticket request has been successfully cancelled.')
            return redirect(reverse('dashboard'))
        else:
            messages.info(self.request, 'You have successfully accepted the offered ticket.')
            return super(AcceptTicketOfferView, self).form_valid(form)
